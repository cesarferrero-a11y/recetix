"""Guardados IA: importa vídeos (TikTok, Instagram...) sobre IA, los resume y los cataloga.

Reutiliza la descarga, la transcripción y la IA de main.py. Tabla propia: public.videos.
"""
import base64
import html
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import main as m

router = APIRouter(prefix="/api/ia")
web = APIRouter()  # páginas de la app (/ia/): archivos sueltos en la raíz del repositorio
BASE = Path(__file__).parent

CATEGORIAS = ["prompts", "tutorial", "agentes y automatización", "código y desarrollo",
              "imágenes y vídeo IA", "productividad", "negocio y dinero",
              "noticias y novedades", "herramienta nueva", "trucos y consejos",
              "aprender IA", "otro"]

PROMPT = """Eres un asistente que cataloga vídeos cortos sobre inteligencia artificial para que su dueño los encuentre luego.
Con la DESCRIPCIÓN y la TRANSCRIPCIÓN del vídeo (puede estar en inglés u otro idioma), responde EN ESPAÑOL.
Devuelve SOLO un JSON con estas claves:
- "titulo": título corto y descriptivo (máx. 80 caracteres), pensado para reconocerlo al buscarlo
- "resumen": 2 o 3 frases con lo esencial
- "puntos_clave": lista de 3 a 6 ideas o pasos concretos del vídeo
- "prompts": lista con los prompts o comandos EXACTOS que se muestren o dicten (copiables). Lista vacía si no hay. No inventes.
- "herramientas": lista con los nombres de las herramientas o modelos mencionados (ej. "Claude", "ChatGPT", "Gemini", "n8n", "Cursor", "NotebookLM", "Midjourney")
- "categoria": exactamente una de {categorias}
- "etiquetas": 3 a 6 etiquetas cortas en minúsculas (ej. "gratis", "sin código", "para probar", "negocio")
- "accion": una frase corta sobre qué podría hacer el usuario con esto (ej. "Copiar el prompt y probarlo en Claude"), o ""

{contexto}"""


# ---------------------------------------------------------------- base de datos
async def sbv(method: str, path: str = "", **kw):
    headers = {"apikey": m.SUPABASE_KEY, "Content-Type": "application/json"}
    if m.SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {m.SUPABASE_KEY}"
    headers.update(kw.pop("headers", {}))
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request(method, f"{m.SUPABASE_URL}/rest/v1/videos{path}", headers=headers, **kw)
    if r.status_code >= 400:
        raise HTTPException(502, f"Base de datos: {r.text[:200]}")
    return r.json() if r.text else None


# ---------------------------------------------------------------- utilidades
def plataforma(url: str) -> str:
    u = url.lower()
    if "tiktok.com" in u or "tiktokv.com" in u:
        return "tiktok"
    if "instagram.com" in u:
        return "instagram"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "x.com" in u or "twitter.com" in u:
        return "x"
    return "web"


def normalizar(url: str) -> str:
    url = url.replace("\\/", "/").rstrip(").,;]»\"'\\")
    mt = re.match(r"https?://(?:www\.)?tiktokv\.com/share/video/(\d+)", url)
    if mt:
        return f"https://www.tiktok.com/@_/video/{mt.group(1)}"
    if "tiktok.com" in url and "/video/" in url:
        url = url.split("?")[0]
    if "instagram.com" in url:
        url = url.split("?")[0]
    return url


async def og_meta(url: str) -> dict:
    """Respaldo para Instagram y webs: lee las etiquetas og: (descripción, imagen)."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uagent.php)",
                                          "Accept-Language": "es,en;q=0.8"})
        if r.status_code != 200:
            return {}
        txt = r.text[:300_000]

        def og(prop):
            mm = (re.search(r'<meta[^>]+property=["\']og:%s["\'][^>]+content=["\']([^"\']*)' % prop, txt, re.I)
                  or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:%s["\']' % prop, txt, re.I))
            return html.unescape(mm.group(1)).strip() if mm else ""

        d = {"title": og("title"), "description": og("description"), "thumbnail": og("image")}
        return d if (d["title"] or d["description"]) else {}
    except Exception:  # noqa: BLE001
        return {}


def limpiar(d: dict) -> dict:
    def s(x):
        return str(x).strip() if x is not None else ""

    def lista(x, n):
        return [s(i) for i in (x or []) if s(i)][:n]

    cat = s(d.get("categoria")).lower()
    herr = []
    for h in lista(d.get("herramientas"), 10):
        if h.lower() not in [x.lower() for x in herr]:
            herr.append(h)
    return {
        "titulo": (s(d.get("titulo")) or "Vídeo sin título")[:120],
        "resumen": s(d.get("resumen")),
        "puntos_clave": lista(d.get("puntos_clave"), 8),
        "prompts": lista(d.get("prompts"), 8),
        "herramientas": herr,
        "categoria": cat if cat in CATEGORIAS else "otro",
        "etiquetas": [t.lower() for t in lista(d.get("etiquetas"), 8)],
        "accion": s(d.get("accion")),
    }


# ---------------------------------------------------------------- API
class Entrada(BaseModel):
    entrada: str
    texto_extra: str = ""


COLS = ("id,created_at,url,plataforma,autor,titulo,resumen,puntos_clave,prompts,herramientas,"
        "categoria,etiquetas,accion,favorito,probado,notas,con_imagen")


@router.get("/videos")
async def listar(q: str = Query(default=""), usuario: str = Depends(m.auth)):
    filtro = ""
    q = re.sub(r"[*,()%\\\"']", " ", q).strip()
    if q:
        patron = quote(f"*{q}*", safe="*")
        filtro = (f"&or=(titulo.ilike.{patron},resumen.ilike.{patron},autor.ilike.{patron},"
                  f"descripcion.ilike.{patron},transcripcion.ilike.{patron},notas.ilike.{patron})")
    return await sbv("GET", f"?select={COLS}&usuario=eq.{usuario}{filtro}&order=created_at.desc&limit=2000")


@router.post("/importar")
async def importar(body: Entrada, usuario: str = Depends(m.auth)):
    mt = m.URL_RE.search(body.entrada)
    url = normalizar(mt.group(0)) if mt else ""
    extra = body.texto_extra.strip()
    descripcion = autor = transcripcion = imagen = ""
    aviso = ""

    if url:
        previa = await sbv("GET", f"?url=eq.{quote(url, safe='')}&usuario=eq.{usuario}&select={COLS}&limit=1")
        if previa:
            return {"video": previa[0], "repetido": True}
        with tempfile.TemporaryDirectory() as tmp:
            info, audio, _err = await m.descargar(url, tmp)
            if not info:
                info = await m.oembed(url) or await og_meta(url)
            descripcion = (info.get("description") or info.get("title") or "").strip()
            autor = (info.get("uploader") or info.get("channel") or "").strip()
            miniatura = info.get("thumbnail") or ""
            if not miniatura:
                miniatura = ((await m.oembed(url)) or (await og_meta(url))).get("thumbnail", "")
            imagen = await m.bajar_miniatura(miniatura, tmp)
            if audio:
                try:
                    transcripcion = await m.transcribir(audio)
                except Exception as e:  # noqa: BLE001
                    aviso = f"No se pudo transcribir el audio ({e})"
    else:
        extra = (body.entrada + "\n" + extra).strip()

    if len(descripcion) + len(transcripcion) + len(extra) < 30:
        raise HTTPException(422, detail={
            "codigo": "sin_media",
            "mensaje": "No he podido sacar texto de este vídeo (Instagram a veces lo bloquea). "
                       "Pega la descripción o lo que dice y lo catalogo."})

    contexto = (f"DESCRIPCIÓN DEL VÍDEO:\n{descripcion}\n\nTRANSCRIPCIÓN DEL AUDIO:\n{transcripcion}\n\n"
                f"TEXTO PEGADO POR EL USUARIO:\n{extra}")[:14000]
    datos = await m.llm_json(PROMPT.format(categorias=", ".join(CATEGORIAS), contexto=contexto))
    fila = limpiar(datos)
    fila.update({"usuario": usuario, "url": url or None, "plataforma": plataforma(url) if url else "texto",
                 "autor": autor, "descripcion": descripcion[:3000], "transcripcion": transcripcion[:30000],
                 "imagen": imagen or None, "con_imagen": bool(imagen)})
    creada = await sbv("POST", "", json=fila, headers={"Prefer": "return=representation"})
    v = creada[0]
    for k in ("imagen", "transcripcion", "descripcion", "usuario"):
        v.pop(k, None)
    return {"video": v, "repetido": False, "aviso": aviso}


class Cambios(BaseModel):
    titulo: str | None = None
    categoria: str | None = None
    etiquetas: list[str] | None = None
    herramientas: list[str] | None = None
    favorito: bool | None = None
    probado: bool | None = None
    notas: str | None = None


@router.patch("/videos/{vid}")
async def editar(vid: str, c: Cambios, usuario: str = Depends(m.auth)):
    m.check_id(vid)
    datos = c.model_dump(exclude_none=True)
    if not datos:
        raise HTTPException(400, "Nada que cambiar")
    r = await sbv("PATCH", f"?id=eq.{vid}&usuario=eq.{usuario}", json=datos,
                  headers={"Prefer": "return=representation"})
    if r:
        for k in ("imagen", "transcripcion", "descripcion", "usuario"):
            r[0].pop(k, None)
    return r[0] if r else {}


@router.delete("/videos/{vid}")
async def borrar(vid: str, usuario: str = Depends(m.auth)):
    m.check_id(vid)
    await sbv("DELETE", f"?id=eq.{vid}&usuario=eq.{usuario}")
    return {"ok": True}


@router.get("/transcripcion/{vid}")
async def transcripcion(vid: str, usuario: str = Depends(m.auth)):
    m.check_id(vid)
    f = await sbv("GET", f"?id=eq.{vid}&usuario=eq.{usuario}&select=transcripcion,descripcion")
    if not f:
        raise HTTPException(404, "No existe")
    return f[0]


@router.get("/img/{vid}")
async def imagen(vid: str):
    m.check_id(vid)
    f = await sbv("GET", f"?id=eq.{vid}&select=imagen")
    if not f or not f[0].get("imagen"):
        raise HTTPException(404, "Sin imagen")
    return Response(base64.b64decode(f[0]["imagen"]), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.post("/videos/{vid}/imagen")
async def refrescar_imagen(vid: str, usuario: str = Depends(m.auth)):
    m.check_id(vid)
    f = await sbv("GET", f"?id=eq.{vid}&usuario=eq.{usuario}&select=url")
    if not f or not f[0].get("url"):
        raise HTTPException(404, "Sin enlace")
    url = f[0]["url"]
    with tempfile.TemporaryDirectory() as tmp:
        mini = ""
        code, out, _ = await m.run(["yt-dlp", "--no-playlist", "--no-warnings", "--skip-download",
                                    "--dump-single-json", url], 60)
        if code == 0:
            try:
                mini = m.json.loads(out).get("thumbnail") or ""
            except Exception:  # noqa: BLE001
                pass
        if not mini:
            mini = ((await m.oembed(url)) or (await og_meta(url))).get("thumbnail", "")
        img = await m.bajar_miniatura(mini, tmp)
    if not img:
        raise HTTPException(502, "No se pudo obtener la portada")
    await sbv("PATCH", f"?id=eq.{vid}&usuario=eq.{usuario}", json={"imagen": img, "con_imagen": True})
    return {"ok": True}


@router.get("/opciones")
async def opciones():
    return {"categorias": CATEGORIAS}


# ---------------------------------------------------------------- páginas de la app
@web.get("/ia")
@web.get("/ia/")
async def ia_index():
    return FileResponse(BASE / "ia_index.html", headers={"Cache-Control": "no-cache"})


@web.get("/ia/manifest.json")
async def ia_manifest():
    return FileResponse(BASE / "ia_manifest.json", media_type="application/manifest+json")


@web.get("/ia/sw.js")
async def ia_sw():
    return FileResponse(BASE / "ia_sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@web.get("/ia/icon-{n}.png")
async def ia_icon(n: str):
    if n not in ("192", "512"):
        raise HTTPException(404, "No existe")
    return FileResponse(BASE / f"ia_icon-{n}.png", media_type="image/png")
