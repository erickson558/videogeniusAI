# Manual de Usuario

## VideoGeniusAI

Version actual: `V0.1.12`

VideoGeniusAI es una aplicacion de escritorio en Python que usa `LM Studio` para escribir el proyecto de video y puede producir el MP4 final de tres formas:

- `Storyboard local`
- `Local AI video` con `ComfyUI + FFmpeg`, voz `Windows local` por defecto y `Piper` opcional
- `Local Avatar video` con `ComfyUI + EchoMimic + VideoHelperSuite`, imagen base del avatar y narracion local

La GUI ahora tiene un flujo rapido llamado `Quick setup` para que escribas el prompt y generes el video completo con un solo boton, y un bloque `Instalacion guiada` para preparar el entorno automaticamente.

La version actual tambien mantiene la ventana visible cuando una posicion guardada queda fuera de pantalla y refuerza la generacion JSON para trabajar mejor con modelos locales de LM Studio.

El flujo `Generar video completo` ahora intenta preparar el entorno automaticamente, abrir LM Studio o ComfyUI si hacen falta y continuar con fallbacks seguros cuando alguno de esos servicios no este listo todavia.

## 1. Requisitos antes de usar la app

Necesitas:

- Windows
- LM Studio instalado o dejar que la app lo instale
- Un modelo cargado en LM Studio
- El servidor local de LM Studio encendido
- FFmpeg instalado o dejar que la app lo instale

Opcional para modo `Local AI video`:

- ComfyUI corriendo localmente
- Un modelo visual cargado en ComfyUI
- Piper instalado si quieres narracion local avanzada
- Un modelo `.onnx` de Piper si quieres narracion local

Opcional para modo `Local Avatar video`:

- ComfyUI corriendo localmente
- Los custom nodes `ComfyUI_EchoMimic` y `ComfyUI-VideoHelperSuite`
- Una imagen base del avatar
- El VAE `sd-vae-ft-mse.safetensors`

Prueba rapida para FFmpeg:

```powershell
ffmpeg -version
```

## 2. Que hace la app

La app trabaja en dos etapas:

### Etapa 1: Generacion del proyecto

LM Studio genera:

- titulo
- resumen
- guion general
- estructura
- escenas
- descripcion por escena
- prompt visual por escena
- narracion por escena
- duracion por escena
- transicion

### Etapa 2: Video final

Existen tres modos:

#### Storyboard local

- crea una imagen `.png` por escena
- si configuras un workflow de imagen de ComfyUI, puede usar imagenes IA en vez de visuales fallback
- ahora divide la escena en varios planos cinematograficos cuando el proyecto trae shot planning
- usa FFmpeg para unirlas
- produce un `.mp4`
- sirve como previsualizacion rapida

#### Local AI video

- usa ComfyUI para generar un clip o gif real por escena
- rechaza workflows que solo generen imagen estatica
- usa Piper para narracion si lo configuras
- quema subtitulos locales si activas captions
- usa FFmpeg para ensamblar el MP4 final
- muestra porcentaje y fase actual del render en la barra de progreso

#### Local Avatar video

- usa ComfyUI con un workflow de avatar o lipsync
- necesita una imagen base del avatar
- usa el audio local de cada escena para conducir el lipsync
- rechaza workflows que solo generen imagen estatica
- usa FFmpeg para ensamblar el MP4 final

### Multi-GPU y rendimiento

- La app detecta las GPUs disponibles de Windows.
- `LM Studio` maneja su propia configuracion de GPU al cargar el modelo.
- `ComfyUI` usa una GPU por instancia mediante `CUDA device index`.
- En `Local AI backend > GPU for Local AI render` puedes elegir la GPU que VideoGeniusAI intentara usar.
- Esa seleccion se aplica cuando la app abre `ComfyUI` automaticamente.
- Si `ComfyUI` ya estaba abierto, la GPU activa depende de como fue iniciada esa instancia.
- Si ejecutas varias instancias de ComfyUI en distintos puertos, la app puede repartir escenas entre todos los workers detectados para acelerar el render.

## 3. Flujo correcto de uso

### Flujo mas simple

1. Escribe un brief creativo en `Project brief`, por ejemplo `Un short cinematografico sobre astronautas perdidos en Marte`.
2. En `Quick setup` elige:
   - estilo visual
   - tono
   - formato
   - duracion
   - backend de video
3. Pulsa `Generar video completo`.

Si el backend es `Storyboard local`, la app genera el proyecto y luego el MP4 en una sola corrida.

Si el backend es `Local AI video`, la app genera el proyecto, intenta preparar y abrir ComfyUI automaticamente y luego arma el MP4 final.

Si LM Studio no responde a tiempo, la app puede generar un proyecto base local para no interrumpir el flujo.

Si ComfyUI no esta listo, la app cambia automaticamente a `Storyboard local` para que el usuario final siga recibiendo un MP4.

### Paso 1: Abrir LM Studio

1. Abre `LM Studio`.
2. Carga un modelo.
3. Ve a `Developer > Local Server`.
4. Enciende el switch del servidor.
5. Verifica que el puerto sea `1234`.

### Paso 2: Abrir VideoGeniusAI y preparar el entorno

1. Abre `videogeniusAI.exe` o `videogeniusAI.pyw`.
2. En `Instalacion guiada` pulsa `Analizar entorno`.
3. Si faltan componentes, pulsa `Preparar entorno automatico`.
4. La app ahora puede:
   - configurar una carpeta compartida de modelos para ComfyUI
   - crear `extra_models_config.yaml`
   - descargar un checkpoint base recomendado
   - crear el workflow inicial por ti
5. Si ComfyUI ya estaba abierto cuando se descargo el checkpoint, reinicialo para que lo detecte.

### Paso 3: Conectar LM Studio

1. En `Base URL` usa:

```text
http://127.0.0.1:1234
```

3. Pulsa `Probar conexion`.

### Paso 4: Llenar el formulario

Completa:

- `Project brief`
- `Visual style`
- `Audience`
- `Narrative tone`
- `Video format`
- `Model`
- `Temperature`
- `Scene count`
- `Output language`
- `Estimated duration (s)`
- `Output folder`

Recomendacion:

- usa `Proyecto completo` si luego quieres producir el MP4

### Paso 5: Generar el proyecto

1. Pulsa `Generar guion`.
2. Espera a que termine.
3. Revisa las pestañas:
   - `Resumen`
   - `Escenas`
   - `JSON`

## 4. Como generar video local desde un prompt

### Opcion A: Storyboard local

1. En `Render backend` elige `Storyboard local`.
2. Si quieres un resultado mas cercano a video IA faceless, deja configurado un workflow de imagen de ComfyUI en `Workflow JSON path`.
3. Pulsa `Generar video final`.
4. Abre la carpeta de salida.

### Opcion B: Local AI video

1. En `Render backend` elige `Local AI video`.
2. En `ComfyUI base URL` usa normalmente:

```text
http://127.0.0.1:8000
```

Si usas ComfyUI no Desktop o una instalacion manual, puede seguir siendo:

```text
http://127.0.0.1:8188
```

3. Pulsa `Preparar entorno automatico`.
4. La app intentara:
   - detectar el modelo visual de ComfyUI
   - detectar las GPUs disponibles para que puedas elegir una en la GUI
   - detectar workers de ComfyUI en puertos locales comunes
   - configurar la carpeta compartida de modelos
   - descargar el modelo base recomendado si aun no existe
   - crear un workflow inicial de imagen estatica solo como referencia tecnica
   - activar `Windows local` para la narracion
   - detectar FFmpeg
5. Si quieres fijar una GPU para el render local:
   - usa `GPU for Local AI render`
   - elige `Auto` o una GPU detectada
   - la app aplicara esa seleccion si necesita abrir `ComfyUI` por ti
6. Si tienes varias instancias de ComfyUI:
   - agrega las URLs en `ComfyUI worker URLs`
   - ejemplo: `http://127.0.0.1:8000, http://127.0.0.1:8189`
   - deja `Parallel workers` igual al numero de workers detectados
7. Si quieres evitar basura visual, llena `Negative prompt`.
8. Si quieres narracion local avanzada:
   - en `TTS backend` elige `Piper local`
   - llena `Piper executable`
   - llena `Piper model`
9. Pulsa `Probar ComfyUI`.
10. Pulsa `Generar video final`.

### Opcion C: Local Avatar video

1. En `Render backend` elige `Local Avatar video`.
2. Usa un workflow JSON de ComfyUI exportado para API que produzca `videos` o `gifs`.
3. En `Avatar source image` selecciona la imagen base del avatar.
4. Usa `Windows local` o `Piper local` para que la app genere el audio por escena.
5. Pulsa `Generar video final`.

## 5. Workflow de ComfyUI

Para `Local AI video`, normalmente si necesitas un workflow manual.

Si `Preparar entorno automatico` detecta un modelo visual, la app crea un JSON base en la carpeta `workflows`, pero ese workflow automatico es de imagen estatica y no sirve como video IA real.

Para `Local AI video` debes usar un workflow personalizado exportado para API que produzca `videos` o `gifs`.

La app espera un workflow JSON exportado para API.

Dentro del workflow puedes usar estos placeholders:

- `__PROMPT__`
- `__NEGATIVE_PROMPT__`
- `__SEED__`
- `__OUTPUT_PREFIX__`
- `__SOURCE_IMAGE__`
- `__AVATAR_IMAGE__`
- `__AUDIO_FILE__`
- `__AUDIO_PATH__`

La app reemplaza esos valores automaticamente por cada escena.

Ejemplo:

- el nodo de prompt positivo puede contener `__PROMPT__`
- el nodo negativo puede contener `__NEGATIVE_PROMPT__`
- el nodo seed puede contener `__SEED__`
- el nodo de prefijo de salida puede contener `__OUTPUT_PREFIX__`

## 6. Que archivos se crean

En storyboard local veras algo como:

```text
20260318_150000_mi_video_storyboard/
  scene_01.png
  scene_02.png

20260318_150000_mi_video.mp4
mi_video_manifest.txt
```

En `Local AI video` veras algo como:

```text
20260318_150000_mi_video_local_ai/
  assets/
  audio/
  subtitles/
  clips/
  concat_manifest.txt

20260318_150000_mi_video_local_ai.mp4
```

Tambien se escribe `log.txt` junto a la app.

- rota automaticamente para no crecer sin limite
- incluye fecha, nivel, modulo, hilo y linea de codigo
- sirve para diagnosticar errores de LM Studio, ComfyUI, FFmpeg y tareas en segundo plano

## 7. Errores comunes y solucion

### Error: no conecta con LM Studio

Solucion:

1. Abre LM Studio
2. Ve a `Developer > Local Server`
3. Activa el servidor
4. Vuelve a pulsar `Probar conexion`

### Error: `Read timed out` o LM Studio piensa demasiado y no devuelve JSON

Solucion:

1. Verifica que el modelo este completamente cargado en LM Studio
2. Prefiere modelos tipo `chat` o `instruct`
3. Evita modelos de razonamiento si necesitas JSON estricto
4. Reduce `Max tokens` si el modelo es lento
5. Vuelve a pulsar `Probar conexion`

### Error: la app no aparece en pantalla

Solucion:

1. Usa la version actual o una posterior
2. Cierra cualquier proceso viejo de la app
3. Vuelve a abrir la app
4. Si aun no aparece, renombra o borra `config.json`
5. Abre otra vez la app para que regenere la configuracion por defecto

### Error: no conecta con ComfyUI

Solucion:

1. Verifica que ComfyUI este abierto
2. Revisa `ComfyUI base URL`
3. Pulsa `Abrir ComfyUI` o `Probar ComfyUI`
4. Luego pulsa `Preparar entorno automatico`

### Error: solo usa una GPU

Solucion:

1. Confirma cuantas GPUs detecta la app en `Instalacion guiada`
2. En `LM Studio`, revisa sus controles de carga de GPU
3. En `GPU for Local AI render`, elige una GPU detectada si la app abrira `ComfyUI` automaticamente
4. Si `ComfyUI` ya estaba abierto, reinicialo con la GPU deseada o deja que VideoGeniusAI lo abra
5. Si quieres acelerar el render IA con ComfyUI, ejecuta una instancia por GPU
6. Asigna un puerto distinto a cada instancia
7. Coloca todas las URLs en `ComfyUI worker URLs`
8. Usa `Parallel workers` igual al numero de instancias activas

### Error: no genera el MP4

Solucion:

```powershell
ffmpeg -version
```

Si falla, instala FFmpeg y vuelve a intentar.

### Error: no hay voz o falla Piper

La opcion recomendada es `Windows local`, porque no requiere instalar nada extra.

Si decides usar Piper:

Causas comunes:

- `Piper executable` incorrecto
- `Piper model` incorrecto
- no elegiste `Piper local`

## 8. Atajos utiles

- `Ctrl+L`: probar conexion LM Studio
- `Ctrl+I`: analizar entorno
- `Ctrl+Shift+I`: preparar entorno automaticamente
- `Ctrl+H`: probar ComfyUI
- `Ctrl+G`: generar guion
- `Ctrl+Shift+G`: generar video completo
- `Ctrl+J`: exportar JSON
- `Ctrl+T`: exportar TXT
- `Ctrl+E`: exportar CSV
- `Ctrl+M`: generar video final
- `Ctrl+O`: abrir carpeta de salida
- `Ctrl+Q`: salir
- `Ctrl+Shift+D`: alternar oscuro/claro
- `F1`: About

## 9. Resumen corto

Si quieres solo la receta practica:

1. Enciende LM Studio
2. Abre la app
3. Pulsa `Preparar entorno automatico`
4. Genera el proyecto
5. Si quieres video local real, abre ComfyUI
6. Pulsa `Probar ComfyUI`
7. Pulsa `Generar video final`
8. Abre la carpeta de salida
9. Usa el `.mp4`
