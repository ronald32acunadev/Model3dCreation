# Especificación Técnica - Fase 5: Optimización, Empaquetado y Roadmap Multiplataforma

**Proyecto:** Motor Local de Generación IA 3D  
**Objetivo:** Optimización de velocidad de inferencia, empaquetado nativo ejecutable y hoja de ruta para Windows y macOS.

---

## 1. Técnicas de Optimización de Inferencia y VRAM

Para maximizar el rendimiento en las dos tarjetas **NVIDIA RTX 5070 de 12 GB**, se aplican tres niveles de optimización en PyTorch:

1. **Precisión Mixta (FP16 / BF16):**
   Uso de  para reducir el consumo de VRAM en un 50% y duplicar la velocidad de paso hacia adelante (*forward pass*).
2. **Compilación de Grafo mediante :**
   Compilar los bloques de atención y difusores con  para fusionar operaciones CUDA.
3. **Offloading de Tensores a la Memoria RAM de 76 GiB:**
   Cuando no se ejecute una etapa específica, los pesos se desplazan a la memoria RAM del sistema, manteniendo libre la VRAM de la GPU.



---

## 2. Empaquetado de la Aplicación de Escritorio en Linux

Se utiliza **PyInstaller** para empaquetar la aplicación PySide6 en un ejecutable binario autocontenido para distribuciones Linux:



El resultado genera la carpeta  que contiene el ejecutable ejecutable en cualquier sistema Ubuntu/Linux.

---

## 3. Hoja de Ruta para Compatibilidad Multiplataforma Futura



---

## 4. Matriz de Criterios de Aceptación y Métricas de Calidad Senior

| Criterio | Métrica Objetivo | Validación Senior |
| :--- | :--- | :--- |
| **Uso de VRAM** | < 11.0 GB en cada GPU RTX 5070 | Previene errores OOM (*Out Of Memory*) permitiendo multitarea. |
| **Tiempo de Inferencia** | < 1.0s (TripoSR) / < 20s (TRELLIS) | Ofrece una experiencia ágil para prototipado rápido y producción final. |
| **Fluidez de la GUI** | 60 FPS estables en el Visor Three.js | La interfaz de usuario no se congela durante la generación del backend. |
| **Compatibilidad GLB** | 100% de éxito de carga en Blender 4.x/5.x | Geometría cerrada sin mallas no-manifold y texturas PBR vinculadas. |
