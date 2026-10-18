# Recetas

Comparte un vídeo de TikTok → la app descarga el audio, lo transcribe (Groq Whisper),
ordena la receta (Gemini) y la cataloga por tipo, cocina y etiquetas (Supabase).

## Puesta en marcha

### 1. Supabase (base de datos)
Pega `schema.sql` en **SQL Editor → New query → Run**. Copia la Project URL y la clave secret
(Project Settings → API Keys). Esa clave solo va en el servidor.

### 2. GitHub (para que Render lea el código)
1. Crea una cuenta en github.com y un repositorio nuevo llamado `recetas` (público; no contiene claves).
2. **Add file → Upload files**: arrastra el contenido de esta carpeta
   (`Dockerfile`, `main.py`, `requirements.txt`, `README.md`, `schema.sql` y la carpeta `static`) → **Commit changes**.

### 3. Render (servidor gratuito, sin tarjeta)
1. En render.com crea cuenta con GitHub → **New → Web Service** → elige el repo `recetas`.
2. **Language/Runtime: Docker**. **Instance type: Free**. Región: Frankfurt.
3. En **Environment Variables** añade:

| Nombre | Valor |
|---|---|
| `GROQ_API_KEY` | tu clave de Groq |
| `GEMINI_API_KEY` | tu clave de Gemini |
| `SUPABASE_URL` | la Project URL de Supabase |
| `SUPABASE_KEY` | la clave secret de Supabase |
| `APP_TOKEN` | una contraseña que te inventes |

4. **Deploy**. Tarda 3-6 minutos. Tu dirección será `https://recetas-xxxx.onrender.com`.
5. Comprueba `https://TU-DIRECCION/api/health`: cuatro `true`.

### 4. Pixel 10
Chrome → abre la dirección → menú ⋮ → **Instalar aplicación**. Escribe tu `APP_TOKEN`.
En TikTok: **Compartir → Más → Recetas**.

## Notas
- El plan gratuito de Render se duerme tras 15 min sin uso: la primera petición tarda ~1 minuto en despertar.
- `GEMINI_MODEL`, `GROQ_STT_MODEL` y `GROQ_LLM_MODEL` se pueden cambiar como variables si algún modelo cambia de nombre.
- «No he podido sacar texto del vídeo»: pega la descripción o la receta y reintenta.
