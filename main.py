"""Recetas: importa recetas desde vídeos (TikTok, etc.) y las cataloga solas.

Flujo: enlace -> yt-dlp (audio + descripción) -> Groq Whisper (transcripción)
       -> Gemini (estructura y etiqueta; Groq LLM como respaldo) -> Supabase.
"""
import asyncio
import hmac
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
GROQ_LLM_MODEL = os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
APP_TOKEN = os.getenv("APP_TOKEN", "")

TIPOS = ["aperitivo", "ensalada", "sopa y crema", "pasta", "arroz", "legumbres",
         "carne", "pescado y marisco", "verdura", "huevos", "pizza y pan",
         "snack y bocadillo", "postre", "bebida", "salsa", "otro"]
COCINAS = ["española", "italiana", "mexicana", "asiática", "americana", "francesa",
           "mediterránea", "árabe", "india", "internacional", "otra"]

BASE = Path(__file__).parent
app = FastAPI(title="Recetas")


# ---------------------------------------------------------------- seguridad
def auth(x_app_token: str = Header(default="")):
    if not APP_TOKEN:
        raise HTTPException(500, "Falta el secreto APP_TOKEN en el servidor")
    if not hmac.compare_digest(x_app_token.encode(), APP_TOKEN.encode()):
        raise HTTPException(401, "Contraseña incorrecta")


UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def check_id(rid: str):
    if not UUID_RE.match(rid):
        raise HTTPException(400, "Id no válido")


# ---------------------------------------------------------------- supabase
async def sb(method: str, path: str = "", **kw):
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    if SUPABASE_KEY.startswith("eyJ"):  # claves antiguas (JWT)
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    headers.update(kw.pop("headers", {}))
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request(method, f"{SUPABASE_URL}/rest/v1/recetas{path}", headers=headers, **kw)
    if r.status_code >= 400:
        raise HTTPException(502, f"Base de datos: {r.text[:200]}")
    return r.json() if r.text else None


# ---------------------------------------------------------------- descarga
_last_update = 0.0


async def run(cmd, timeout):
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124, "", "timeout"
    return p.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")


async def actualizar_ytdlp():
    """TikTok cambia a menudo: si falla, actualizamos yt-dlp (máx. 1 vez cada 6 h)."""
    global _last_update
    if time.time() - _last_update < 6 * 3600:
        return False
    _last_update = time.time()
    code, _, _ = await run(["pip", "install", "-U", "--no-cache-dir", "yt-dlp"], 180)
    return code == 0


async def descargar(url: str, tmp: str):
    """Devuelve (info_dict, ruta_audio|None, error|None)."""
    base = ["yt-dlp", "--no-playlist", "--no-warnings", "--socket-timeout", "20",
            "--retries", "2", "--max-filesize", "150M", "-x", "--audio-format", "mp3",
            "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000 -b:a 32k",
            "--write-info-json", "-o", f"{tmp}/media.%(ext)s"]
    audio = Path(tmp) / "media.mp3"
    err = ""
    for ronda in range(2):
        for extra in ([], ["--impersonate", "chrome"]):
            code, _, err = await run(base + extra + [url], 120)
            if audio.exists():
                break
            # sin audio pero con info (p. ej. publicación sin sonido): nos vale la info
            if (Path(tmp) / "media.info.json").exists():
                break
        if audio.exists() or (Path(tmp) / "media.info.json").exists():
            break
        if ronda == 0 and not await actualizar_ytdlp():
            break
    info = {}
    ij = Path(tmp) / "media.info.json"
    if ij.exists():
        try:
            info = json.loads(ij.read_text(encoding="utf-8"))
        except Exception:
            info = {}
    return info, (audio if audio.exists() else None), (err[-300:] if not info and not audio.exists() else None)


async def oembed(url: str) -> dict:
    if "tiktok.com" not in url:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get("https://www.tiktok.com/oembed", params={"url": url})
        if r.status_code == 200:
            d = r.json()
            return {"description": d.get("title", ""), "uploader": d.get("author_name", ""),
                    "title": d.get("title", "")}
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------- IA
async def transcribir(audio: Path) -> str:
    if not GROQ_API_KEY:
        return ""
    if audio.stat().st_size > 24 * 1024 * 1024:
        return ""
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": ("audio.mp3", audio.read_bytes(), "audio/mpeg")},
            data={"model": GROQ_STT_MODEL, "response_format": "json", "temperature": "0"})
    if r.status_code != 200:
        raise RuntimeError(f"Groq STT {r.status_code}: {r.text[:200]}")
    return r.json().get("text", "").strip()


PROMPT = """Eres un asistente de cocina. Con la DESCRIPCIÓN y la TRANSCRIPCIÓN de un vídeo, extrae la receta EN ESPAÑOL.
Devuelve SOLO un JSON con estas claves:
- "es_receta": true/false (false si el vídeo no explica cómo preparar un plato o bebida)
- "titulo": nombre corto del plato
- "descripcion": una frase
- "raciones": texto (ej. "4 personas") o ""
- "tiempo": texto (ej. "30 min") o ""
- "ingredientes": lista de {{"cantidad": "200 g", "nombre": "harina"}}. Si no se dice la cantidad, deja "" y NO la inventes.
- "pasos": lista de textos, en orden y claros. No inventes pasos que no aparezcan.
- "tipo": exactamente uno de {tipos}
- "cocina": exactamente una de {cocinas}
- "etiquetas": 3 a 6 etiquetas cortas en minúsculas (ej. "rápida", "airfryer", "sin horno", "vegetariana", "económica").

{contexto}"""


def _json_de(texto: str) -> dict:
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.M).strip()
    return json.loads(texto)


async def llm_json(prompt: str) -> dict:
    errores = []
    if GEMINI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=90) as c:
                r = await c.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                    headers={"x-goog-api-key": GEMINI_API_KEY},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json",
                                               "temperature": 0.2}})
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
            partes = r.json()["candidates"][0]["content"]["parts"]
            return _json_de("".join(p.get("text", "") for p in partes))
        except Exception as e:  # noqa: BLE001
            errores.append(f"Gemini {e}")
    if GROQ_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=90) as c:
                r = await c.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    json={"model": GROQ_LLM_MODEL, "temperature": 0.2,
                          "response_format": {"type": "json_object"},
                          "messages": [{"role": "user", "content": prompt}]})
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
            return _json_de(r.json()["choices"][0]["message"]["content"])
        except Exception as e:  # noqa: BLE001
            errores.append(f"Groq {e}")
    raise HTTPException(502, "La IA no respondió. " + " | ".join(errores)[:300])


def limpiar(d: dict) -> dict:
    def s(x):
        return str(x).strip() if x is not None else ""

    ing = []
    for i in d.get("ingredientes") or []:
        if isinstance(i, dict):
            ing.append({"cantidad": s(i.get("cantidad")), "nombre": s(i.get("nombre"))})
        elif i:
            ing.append({"cantidad": "", "nombre": s(i)})
    tipo = s(d.get("tipo")).lower()
    cocina = s(d.get("cocina")).lower()
    tags = [s(t).lower() for t in (d.get("etiquetas") or []) if s(t)][:8]
    return {
        "titulo": s(d.get("titulo")) or "Receta sin título",
        "descripcion": s(d.get("descripcion")),
        "raciones": s(d.get("raciones")),
        "tiempo": s(d.get("tiempo")),
        "ingredientes": [i for i in ing if i["nombre"]],
        "pasos": [s(p) for p in (d.get("pasos") or []) if s(p)],
        "tipo": tipo if tipo in TIPOS else "otro",
        "cocina": cocina if cocina in COCINAS else "otra",
        "etiquetas": tags,
    }


# ---------------------------------------------------------------- API
class Entrada(BaseModel):
    entrada: str                 # enlace (o texto compartido que contiene el enlace) o receta pegada
    texto_extra: str = ""        # descripción pegada a mano si la descarga falla


URL_RE = re.compile(r"https?://[^\s]+")


def normalizar(url: str) -> str:
    url = url.rstrip(").,;]»\"'")
    if "tiktok.com" in url and "/video/" in url:
        url = url.split("?")[0]
    return url


@app.get("/api/health")
async def health():
    return {"ok": True, "groq": bool(GROQ_API_KEY), "gemini": bool(GEMINI_API_KEY),
            "supabase": bool(SUPABASE_URL and SUPABASE_KEY), "token": bool(APP_TOKEN)}


@app.get("/api/recetas", dependencies=[Depends(auth)])
async def listar():
    cols = ("id,created_at,url,titulo,descripcion,raciones,tiempo,ingredientes,pasos,"
            "tipo,cocina,etiquetas,favorita,autor,notas")
    return await sb("GET", f"?select={cols}&order=created_at.desc&limit=1000")


@app.post("/api/importar", dependencies=[Depends(auth)])
async def importar(body: Entrada):
    m = URL_RE.search(body.entrada)
    url = normalizar(m.group(0)) if m else ""
    contexto_extra = body.texto_extra.strip()
    descripcion = autor = transcripcion = ""
    aviso = ""

    if url:
        previa = await sb("GET", f"?url=eq.{quote(url, safe='')}&select=*&limit=1")
        if previa:
            return {"receta": previa[0], "repetida": True}
        with tempfile.TemporaryDirectory() as tmp:
            info, audio, err = await descargar(url, tmp)
            if not info:
                info = await oembed(url)
            descripcion = (info.get("description") or info.get("title") or "").strip()
            autor = (info.get("uploader") or info.get("channel") or "").strip()
            if audio:
                try:
                    transcripcion = await transcribir(audio)
                except Exception as e:  # noqa: BLE001
                    aviso = f"No se pudo transcribir el audio ({e})"
            elif not info:
                aviso = "No se pudo descargar el vídeo"
    else:
        contexto_extra = (body.entrada + "\n" + contexto_extra).strip()

    contexto = (f"DESCRIPCIÓN DEL VÍDEO:\n{descripcion}\n\nTRANSCRIPCIÓN DEL AUDIO:\n{transcripcion}\n\n"
                f"TEXTO PEGADO POR EL USUARIO:\n{contexto_extra}")[:14000]
    if len(descripcion) + len(transcripcion) + len(contexto_extra) < 40:
        raise HTTPException(422, detail={
            "codigo": "sin_media",
            "mensaje": "No he podido sacar texto del vídeo. Pega la descripción o la receta y lo intento de nuevo."})

    datos = await llm_json(PROMPT.format(tipos=", ".join(TIPOS), cocinas=", ".join(COCINAS),
                                         contexto=contexto))
    if datos.get("es_receta") is False:
        raise HTTPException(422, detail={"codigo": "no_receta",
                                         "mensaje": "Esto no parece una receta."})
    fila = limpiar(datos)
    fila.update({"url": url or None, "autor": autor, "transcripcion": transcripcion[:20000]})
    creada = await sb("POST", "", json=fila, headers={"Prefer": "return=representation"})
    return {"receta": creada[0], "repetida": False, "aviso": aviso}


class Cambios(BaseModel):
    titulo: str | None = None
    tipo: str | None = None
    cocina: str | None = None
    etiquetas: list[str] | None = None
    favorita: bool | None = None
    notas: str | None = None


@app.patch("/api/recetas/{rid}", dependencies=[Depends(auth)])
async def editar(rid: str, c: Cambios):
    check_id(rid)
    datos = c.model_dump(exclude_none=True)
    if not datos:
        raise HTTPException(400, "Nada que cambiar")
    r = await sb("PATCH", f"?id=eq.{rid}", json=datos, headers={"Prefer": "return=representation"})
    return r[0] if r else {}


@app.delete("/api/recetas/{rid}", dependencies=[Depends(auth)])
async def borrar(rid: str):
    check_id(rid)
    await sb("DELETE", f"?id=eq.{rid}")
    return {"ok": True}


@app.get("/api/opciones")
async def opciones():
    return {"tipos": TIPOS, "cocinas": COCINAS}


# ---------------------------------------------------------------- estáticos
@app.get("/sw.js")
async def sw():
    return FileResponse(BASE / "static" / "sw.js", media_type="application/javascript")


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")
