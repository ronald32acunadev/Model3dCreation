# Especificación Técnica - Fase 2: Motor Backend & Estrategia Dual-GPU

**Proyecto:** Motor Local de Generación IA 3D  
**Entorno de Referencia:** Ubuntu 26.04.1 LTS | 2x NVIDIA RTX 5070 12 GB | 76 GiB RAM  
**Objetivo:** Construcción del servidor backend asíncrono en FastAPI y la estrategia de asignación de cargas entre ambas GPUs.

---

## 1. Estrategia Arquitectónica de Asignación Dual-GPU

La topología de tarjetas de video en el sistema presenta diferencias en la velocidad de bus PCIe:
* **GPU 1 (Bus 29:00.0):** PCIe 3.0 x16 -> **Mayor Ancho de Banda**.
* **GPU 0 (Bus 25:00.0):** PCIe 2.0 x2 -> **Menor Ancho de Banda**.

Para maximizar el rendimiento sin cuellos de botella en la transferencia de tensores a través del bus del chipset B450, aplicamos **Pipeline Parallelism Asimétrico**:

cuda:0cuda:1

---

## 2. Código del Gestor de GPUs ()



---

## 3. Servidor Backend Principal ()


