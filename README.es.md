# Model3D

[English](README.md) · [Español](README.es.md)

[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.9.1%20cu130-EE4C2C.svg)](https://pytorch.org/)
[![PySide6](https://img.shields.io/badge/PySide6-6.12.0-41CD52.svg)](https://wiki.qt.io/Qt_for_Python)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)
[![Blender](https://img.shields.io/badge/Blender-4.2%2B-F5792A.svg)](https://www.blender.org/)
[![Licencia: GPL-3.0-or-later](https://img.shields.io/badge/Licencia-GPL--3.0--or--later-blue.svg)](blender/model3d/blender_manifest.toml)

**Model3D** es un pipeline local acelerado por doble GPU que transforma imágenes 2D individuales en modelos 3D texturizados y con esqueletos de animación (`.glb`) mediante **Hunyuan3D 2.1** y **UniRig**. Diseñado específicamente para estaciones de trabajo con dos tarjetas NVIDIA RTX 5070 en Linux x86_64, el proyecto cuenta con un motor de cómputo asíncrono desacoplado, una aplicación de escritorio con interfaz gráfica (PySide6) y una extensión nativa para Blender 4.2+.

---

## Tabla de Contenidos

- [Características Principales](#características-principales)
- [Arquitectura del Sistema](#arquitectura-del-sistema)
- [Estructura del Repositorio](#estructura-del-repositorio)
- [Objetivo de Hardware y Modos GPU](#objetivo-de-hardware-y-modos-gpu)
- [Requisitos Previos y Herramientas](#requisitos-previos-y-herramientas)
- [Dependencias Externas y Parches](#dependencias-externas-y-parches)
- [Instalación y Guía Rápida](#instalación-y-guía-rápida)
  - [1. Configuración de Subproyectos](#1-configuración-de-subproyectos)
  - [2. Ejecución del Motor](#2-ejecución-del-motor)
  - [3. Ejecución de la Aplicación de Escritorio](#3-ejecución-de-la-aplicación-de-escritorio)
  - [4. Instalación del Add-on de Blender](#4-instalación-del-add-on-de-blender)
- [Referencia de Configuración](#referencia-de-configuración)
- [Referencia de la API REST y WebSockets](#referencia-de-la-api-rest-y-websockets)
- [Scripts de Diagnóstico y Benchmarking](#scripts-de-diagnóstico-y-benchmarking)
- [Pruebas y Aseguramiento de Calidad](#pruebas-y-aseguramiento-de-calidad)
- [Licencia](#licencia)

---

## Características Principales

- **Arquitectura Desacoplada Motor-Cliente**: La inferencia pesada de PyTorch en GPU se ejecuta en un proceso en segundo plano independiente (`model3d_engine`), manteniendo el hilo de la interfaz gráfica 100% fluido.
- **Aceleración por Pipeline Dual-GPU**: Asigna automáticamente la generación de geometría (Hunyuan3D Etapa 1) a una GPU residente y el renderizado/síntesis de texturas PBR (Hunyuan3D Etapa 2) junto con el rigging esquelético (UniRig) a la segunda GPU.
- **Modos de Degradación Elegante**: Se adapta de forma inteligente al hardware disponible (`dual_gpu`, `single_gpu_resident`, `single_gpu_swap`, `shape_only`, `unsupported`).
- **Cliente de Escritorio Interactivo**: Aplicación moderna en PySide6 con arquitectura MVVM, supervisor automático del motor, soporte bilingüe (español/inglés) y visor 3D integrado mediante QtWebEngine.
- **Add-on Nativo para Blender 4.2+**: Extensión directa para Blender que permite generar mallas, monitorizar trabajos, navegar por la librería e importar modelos riggeados directamente en la escena sin dependencias externas adicionales (`urllib` de la biblioteca estándar).

---

## Arquitectura del Sistema

```text
+-----------------------------------------------------------------------------------+
|                                 CAPA DE CLIENTES                                  |
|                                                                                   |
|  +-------------------------------------+   +-----------------------------------+  |
|  |     Cliente de Escritorio PySide6   |   |       Add-on de Blender 4.2+      |  |
|  |   - MVVM (Views/ViewModels)        |   |   - Extensión nativa (urllib)     |  |
|  |   - EngineSupervisor               |   |   - Importador directo a escena   |  |
|  |   - Vista 3D con QtWebEngine        |   |   - Controles de Rig y Textura    |  |
|  +-------------------------------------+   +-----------------------------------+  |
+---------------------------|----------------------------------|--------------------+
                            | HTTP (REST) y WebSockets         | HTTP (REST)
                            v                                  v
+-----------------------------------------------------------------------------------+
|                        MOTOR LOCAL DE INFERENCIA (FastAPI)                        |
|                                                                                   |
|  +--------------------+   +-----------------------+   +------------------------+  |
|  |  LoopbackGuard y   |   |  JobStore y EventBus  |   |    Planificador        |  |
|  |  Endpoints API     |-->|  - Almacén en disco   |-->|    Pipelined           |  |
|  |  (/v1/jobs, etc.)  |   |  - Eventos en vivo    |   |    Multi-Etapa         |  |
|  +--------------------+   +-----------------------+   +------------------------+  |
|                                                                   |               |
|         +---------------------------------------------------------+               |
|         |                                                         |               |
|         v (Asignación GPU)                                        v               |
|  +------------------------------------+   +------------------------------------+  |
|  |       GPU A (Enlace Principal)     |   |       GPU B (Enlace Secundario)    |  |
|  |   Worker Shape (Hunyuan3D S1)      |   |   Worker Paint (Hunyuan3D S2)      |  |
|  |   Generación de Geometría y Malla  |   |   Texturas PBR y Rigging UniRig    |  |
|  +------------------------------------+   +------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## Estructura del Repositorio

El espacio de trabajo se organiza en subproyectos independientes gestionados por `uv`:

| Directorio | Rol | Descripción |
| :--- | :--- | :--- |
| [`contracts/`](contracts/) | Modelos Compartidos | Esquemas Pydantic v2 (`Job`, `JobOptions`, `JobState`, `Health`, `JobError`) compartidos entre el motor y los clientes. |
| [`engine/`](engine/) | Backend de Inferencia | Servidor FastAPI (`python -m model3d_engine`), ejecutado sobre Python 3.11, CUDA 13.0 PyTorch y ruedas precompiladas de `bpy`. |
| [`client/`](client/) | Aplicación de Escritorio | Interfaz gráfica en PySide6 (`python -m model3d_client`), visor QtWebEngine y supervisor de ciclo de vida del motor. |
| [`blender/`](blender/) | Add-on de Blender | Extensión Blender 4.2+ en `blender/model3d/` con suite de pruebas en Python. |
| [`scripts/`](scripts/) | Herramientas y Diagnóstico | Verificación de topología de hardware (`verify_gpus.py`) y medición de VRAM por etapa (`measure_stage_vram.py`). |
| [`Fases/`](Fases/) | Especificaciones Técnicas | Documentos de arquitectura y especificación por fases del proyecto. |

---

## Objetivo de Hardware y Modos GPU

El pipeline está calibrado para dos tarjetas **NVIDIA RTX 5070 (12 GB VRAM cada una)** en Linux x86_64.

Al arrancar, el motor evalúa la topología con `nvidia-smi` y selecciona automáticamente uno de cinco modos de ejecución:

| Modo | Descripción | Requisito GPU |
| :--- | :--- | :--- |
| `dual_gpu` | Shape se mantiene residente en una tarjeta; Paint y UniRig operan en la otra. Cero penalización por intercambio de memoria. | 2 GPUs NVIDIA utilizables con >= 10 GiB VRAM libre cada una. |
| `single_gpu_resident` | Ambos modelos (geometría y texturas) permanecen cargados simultáneamente en memoria. | 1 GPU con >= 22 GiB VRAM libre. |
| `single_gpu_swap` | Los workers de shape y paint alternan en un único dispositivo, liberando pesos entre etapas. | 1 GPU con 10–21 GiB VRAM libre. |
| `shape_only` | Únicamente generación geométrica; el pintado de texturas permanece inactivo. | 1 GPU con 10–12 GiB VRAM libre. |
| `unsupported` | El motor arranca en estado de error; la API notifica las causas a través de `/v1/health`. | VRAM insuficiente (< 10 GiB) o ausencia de GPUs CUDA. |

---

## Requisitos Previos y Herramientas

- **Sistema Operativo**: Linux x86_64 (e.g., Ubuntu 24.04 / 26.04 LTS)
- **Controlador NVIDIA**: Versión 570+ (soporte para CUDA 12.8 / 13.0+)
- **Python**: Python 3.11 (estrictamente requerido por `engine/` debido a las ruedas cp311 de `bpy==4.5.*`)
- **Gestor de Paquetes**: [`uv`](https://docs.astral.sh/uv/) (>= 0.5.0)
- **Compiladores**: `gcc`, `g++`, `ninja` y `nvcc` correspondiente al `CUDA_HOME` para rasterizadores en C++/CUDA.

---

## Dependencias Externas y Parches

En el directorio `vendor/` residen dos repositorios externos no versionados en git:

1. **Hunyuan3D 2.1**:
   ```bash
   git clone https://github.com/Tencent/Hunyuan3D-2.1 vendor/Hunyuan3D-2.1
   # Aplicar parche para remover dispositivos cuda:0 fijados:
   git -C vendor/Hunyuan3D-2.1 apply ../../engine/patches/hunyuan3d-paint-device.patch
   ```
2. **UniRig**:
   ```bash
   git clone https://github.com/VAST-AI-Research/UniRig vendor/UniRig
   # Configurar el entorno virtual dedicado de UniRig dentro de su carpeta
   ```

---

## Instalación y Guía Rápida

### 1. Configuración de Subproyectos

Sincroniza las dependencias de cada componente utilizando `uv`:

```bash
# Contratos compartidos
cd contracts && uv sync && cd ..

# Aplicación de escritorio
cd client && uv sync && cd ..

# Entorno de pruebas del add-on de Blender
cd blender && uv sync && cd ..

# Motor de inferencia (requiere CUDA_HOME y el clon de Hunyuan3D-2.1 para custom_rasterizer)
cd engine && uv sync && cd ..
```

### 2. Ejecución del Motor

Inicia el motor de cálculo directamente:

```bash
uv run --project engine python -m model3d_engine
```

El motor comenzará a responder en `http://127.0.0.1:8765`. Tras el inicio, inspecciona los GPUs y carga los modelos en segundo plano mientras `/v1/health` reporta el estado.

### 3. Ejecución de la Aplicación de Escritorio

Lanza la interfaz gráfica de PySide6:

```bash
# Ejecución directa con uv
uv run --project client python -m model3d_client

# O mediante el script lanzador (utilizado por el acceso directo .desktop)
./client/launch.sh
```

> **Nota**: Si el motor no está corriendo previamente, el componente `EngineSupervisor` del cliente levantará automáticamente `engine/.venv/bin/python -m model3d_engine` y lo cerrará al salir.

### 4. Instalación del Add-on de Blender

`blender/model3d` es una extensión nativa para Blender 4.2+:

1. Abre Blender 4.2 o posterior.
2. Dirígete a **Edit** > **Preferences** > **Add-ons / Extensions**.
3. Instala desde archivo o crea un enlace simbólico hacia tu carpeta de extensiones:
   ```bash
   ln -s "$(pwd)/blender/model3d" "$HOME/.config/blender/4.2/extensions/user_default/model3d"
   ```
4. Activa el add-on **Model3D** en la barra lateral del 3D Viewport (tecla `N`, pestaña `Model3D`).

---

## Referencia de Configuración

El motor se configura exclusivamente a través de variables de entorno:

| Variable | Tipo | Valor por Defecto | Descripción |
| :--- | :--- | :--- | :--- |
| `MODEL3D_HOME` | Ruta | `data/` | Directorio base para trabajos (`$MODEL3D_HOME/jobs`) y caché HuggingFace (`$MODEL3D_HOME/hf`). |
| `MODEL3D_HUNYUAN_DIR` | Ruta | `vendor/Hunyuan3D-2.1` | Ruta al clon del repositorio de Hunyuan3D 2.1. |
| `MODEL3D_UNIRIG_DIR` | Ruta | `vendor/UniRig` | Ruta al clon del repositorio de UniRig. |
| `MODEL3D_HOST` | Texto | `127.0.0.1` | Dirección de escucha para HTTP y WebSockets. |
| `MODEL3D_PORT` | Entero | `8765` | Puerto del motor. |
| `MODEL3D_MODE` | Texto | `None` (auto) | Forzado de modo: `dual_gpu`, `single_gpu_resident`, `single_gpu_swap` o `shape_only`. |
| `MODEL3D_SHAPE_BUS` | Texto | `None` (auto) | ID del bus PCI para generación de malla (ej: `0000:29:00.0`). Requiere `MODEL3D_PAINT_BUS`. |
| `MODEL3D_PAINT_BUS` | Texto | `None` (auto) | ID del bus PCI para pintado y rigging (ej: `0000:01:00.0`). Requiere `MODEL3D_SHAPE_BUS`. |
| `MODEL3D_ALLOWED_ORIGINS`| Texto | `""` | Orígenes web permitidos para protección CORS/CSRF (separados por coma). |
| `MODEL3D_STAGE_TIMEOUT_S`| Float | `900.0` | Tiempo límite en segundos para una etapa antes de reiniciar el worker. |

---

## Referencia de la API REST y WebSockets

El servidor expone una API protegida contra DNS rebinding y CSRF (`LoopbackGuard`):

### Endpoints

| Método | Endpoint | Descripción | Códigos HTTP |
| :--- | :--- | :--- | :--- |
| `GET` | `/v1/health` | Estado de disponibilidad, modo de GPU seleccionado y memoria de tarjetas. | `200 OK` |
| `POST` | `/v1/jobs` | Enviar nuevo trabajo de generación imagen a 3D (`multipart/form-data`: `image`, `options`). | `202 Accepted`, `400`, `413`, `503` |
| `POST` | `/v1/rig-jobs` | Enviar modelo `.glb` existente para rig esquelético automático (`multipart/form-data`: `model`). | `202 Accepted`, `400`, `413`, `503` |
| `GET` | `/v1/jobs/{job_id}` | Consultar estado, progreso y etapa actual de un trabajo. | `200 OK`, `404 Not Found` |
| `GET` | `/v1/jobs/{job_id}/result`| Descargar el modelo binario generado o riggeado `model.glb`. | `200 OK`, `404`, `409 Conflict` |
| `DELETE` | `/v1/jobs/{job_id}` | Cancelar un trabajo en cola o en ejecución. | `202 Accepted`, `404`, `409` |
| `WS` | `/v1/jobs/{job_id}/events` | Canal WebSocket para eventos en tiempo real (progreso, cambios de fase, errores). | Upgrade de conexión |

### Opciones de Trabajo (`options` JSON en `POST /v1/jobs`)

```json
{
  "texture": true,
  "quality": "final",
  "seed": 42,
  "remove_background": true,
  "target_faces": 40000,
  "rig": false
}
```

- **`quality`**: `"draft"`, `"final"` o `"high"`.
- **`target_faces`**: Entero >= 1000 (meta opcional de decimado de polígonos).
- **`rig`**: Booleano para añadir automáticamente un esqueleto de huesos mediante UniRig tras el texturizado.

---

## Scripts de Diagnóstico y Benchmarking

Rutinas de soporte ubicadas en [`scripts/`](scripts/):

```bash
# 1. Verificar topología dual-GPU, ancho de banda PCIe y entorno CUDA
uv run --project engine python scripts/verify_gpus.py

# 2. Medir consumo de VRAM por etapa bajo carga sintética
# (Nota: Descargar previamente modelos de LM Studio u Ollama)
uv run --project engine python scripts/measure_stage_vram.py
```

Para actualizar los catálogos de internacionalización del cliente (extracción a `model3d_es.ts` y compilación a `model3d_es.qm`):

```bash
client/scripts/update_translations.sh
```

---

## Pruebas y Aseguramiento de Calidad

Cada subproyecto cuenta con una batería de pruebas automatizadas mediante `pytest`:

```bash
# 1. Pruebas de modelos de contratos (17 superadas)
(cd contracts && uv run pytest)

# 2. Pruebas unitarias de vistas, MVVM y supervisor del cliente (111 superadas)
(cd client && uv run pytest)

# 3. Pruebas del cliente y rastreador del add-on de Blender (25 superadas)
(cd blender && uv run pytest)

# 4. Pruebas de API, planificador de GPU y almacenamiento del motor (al configurar vendor)
(cd engine && uv run pytest)
```

### Marcadores Personalizados de Pytest

- **`-m gpu`**: Pruebas que requieren las dos tarjetas físicas RTX 5070 con modelos cargados (excluidas por defecto en CI).
- **`-m webengine`**: Pruebas que necesitan QtWebEngine con contexto gráfico acelerado por hardware.
- **`-m blender`**: Pruebas que invocan el binario de Blender en modo headless (`blender -b`).

---

## Licencia

- El add-on de Blender (`blender/model3d/`) está bajo licencia **GPL-3.0-or-later** según se especifica en [`blender/model3d/blender_manifest.toml`](blender/model3d/blender_manifest.toml).
- Las bibliotecas web de terceros en `client/src/model3d_client/views/viewer/vendor/` respetan sus licencias de código abierto detalladas en `VENDOR.md`.
