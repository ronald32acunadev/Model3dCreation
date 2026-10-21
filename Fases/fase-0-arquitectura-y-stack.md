# Especificación Técnica - Fase 0: Arquitectura General y Stack Tecnológico

**Proyecto:** Motor Local de Generación IA 3D & Aplicación de Escritorio Multiplataforma
**Entorno de Referencia:** Ubuntu 26.04.1 LTS | AMD Ryzen 7 5800X (8C/16T) | 76 GiB RAM | 2x NVIDIA RTX 5070 (12 GB VRAM ea)
**Nivel de Arquitectura:** Senior Software Architecture Spec

---

## 1. Visión General de la Arquitectura

El proyecto adopta una **Arquitectura Desacoplada Motor-Cliente** (*Decoupled Client-Engine Architecture*). Esta separación física y lógica entre el motor de cálculo de Inteligencia Artificial (PyTorch/CUDA) y la interfaz de usuario de escritorio garantiza que las cargas computacionales intensivas no bloqueen el bucle de eventos principal de la aplicación gráfica (*UI Event Loop*).



---

## 2. Mapeo del Hardware Específico del Sistema

| Componente de Hardware | Especificación del Documento | Asignación en la Arquitectura de IA |
| :--- | :--- | :--- |
| **CPU** | AMD Ryzen 7 5800X (8C/16T, 4.85 GHz) | Procesamiento multihilo de conversión de mallas, posprocesamiento de texturas y gestión del servidor API. |
| **Memoria RAM** | 76 GiB DDR4 utilizables | Caché en RAM de pesados checkpoints PyTorch (~30 GB), evitando recargas desde SSD durante inferencias continuas. |
| **GPU 1 (Primaria)** | NVIDIA RTX 5070 12 GB (PCIe 3.0 x16) | **** - Inferencia de alta transferencia de geometría (TRELLIS / Hunyuan3D Stage 1). |
| **GPU 0 (Secundaria)** | NVIDIA RTX 5070 12 GB (PCIe 2.0 x2) | **** - Inferencia de refinamiento de texturas PBR y mapas normales (Hunyuan3D Stage 2). |
| **Almacenamiento** | NVMe Kingston KC3000 1 TB | Instalación del entorno, caché de modelos HuggingFace/PyTorch y temp-files de exportación GLB. |
| **Sistema Operativo** | Ubuntu 26.04.1 LTS (Kernel 7.0) | Entorno nativo Linux x86_64 con drivers NVIDIA 595.99.02 instalados. |

---

## 3. Estructura Completa del Repositorio de Código



---

## 4. Definición de Contratos API (REST & WebSockets)

### Endpoint REST: POST 
* **Request Payload (JSON):**


* **Response Payload (JSON):**


### Evento WebSocket ()
* **Mensaje de Progreso (Servidor -> Cliente):**


* **Mensaje de Finalización:**

