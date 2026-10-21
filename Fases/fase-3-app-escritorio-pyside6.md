# Especificación Técnica - Fase 3: Aplicación de Escritorio Nativa (PySide6)

**Proyecto:** Motor Local de Generación IA 3D  
**Framework GUI:** PySide6 (Qt6 for Python) + PySide6.QtWebEngineWidgets  
**Renderizador 3D:** Three.js embebido en HTML5/WebGL  

---

## 1. Arquitectura de la Interfaz Gráfica (Patrón MVVM + Async Workers)

Para evitar el bloqueo de la interfaz de usuario durante las llamadas a la API de generación, la aplicación separa la interfaz en tres capas:
1. **Vista (Qt UI Thread):** Ventana principal y componentes visuales ().
2. **Visor 3D (QWebEngineView):** Contexto WebGL aislado corriendo Three.js para la inspección interactiva de mallas .
3. **Hilo Trabajador (QThread / Worker):** Comunicación asíncrona mediante HTTP y WebSockets con el backend FastAPI.



---

## 2. Código del Visor Three.js HTML ()



---

## 3. Código de la Ventana Principal ()


