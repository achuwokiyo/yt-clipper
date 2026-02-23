# YT Clipper 🎬

Extrae clips de YouTube directamente desde el navegador — móvil y PC.

## Deploy en Railway (gratis, 5 minutos)

### 1. Crear cuenta en Railway
Ve a [railway.app](https://railway.app) y regístrate con GitHub.

### 2. Subir el proyecto a GitHub
1. Crea un repositorio nuevo en [github.com](https://github.com)
2. Sube todos estos archivos al repositorio

### 3. Deploy en Railway
1. En Railway, haz clic en **"New Project"**
2. Selecciona **"Deploy from GitHub repo"**
3. Elige tu repositorio
4. Railway detecta el `Dockerfile` automáticamente y hace el deploy

### 4. Obtener la URL
Una vez desplegado, Railway te da una URL pública tipo:
```
https://yt-clipper-production.up.railway.app
```
¡Accede desde móvil o PC!

---

## Deploy en Render (alternativa gratuita)

1. Ve a [render.com](https://render.com) y regístrate
2. **New → Web Service → Connect a repository**
3. Selecciona tu repo
4. Configuración:
   - **Environment:** Docker
   - **Plan:** Free
5. Clic en **Create Web Service**

---

## Uso local (opcional)

```bash
# Instalar dependencias del sistema
# Mac: brew install ffmpeg
# Linux: sudo apt install ffmpeg

pip install -r requirements.txt
python app.py
# Abrir http://localhost:5000
```

---

## Estructura del proyecto
```
yt-clipper/
├── app.py              # Backend Flask
├── requirements.txt    # Dependencias Python
├── Dockerfile          # Configuración Docker
├── README.md
└── templates/
    └── index.html      # Interfaz web
```
