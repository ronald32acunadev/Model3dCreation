"""Spanish for the add-on's interface, registered with bpy.app.translations.

Keys are (context, English text); "*" is the default context Blender uses for UI labels, and
"Operator" the one for operator button labels.
"""

SPANISH = {
    "Model3D": "Model3D",
    "Image": "Imagen",
    "Quality": "Calidad",
    "Draft": "Borrador",
    "Final": "Final",
    "High detail": "Alto detalle",
    "Texture": "Textura",
    "With skeleton": "Con esqueleto",
    "Generate": "Generar",
    "Cancel": "Cancelar",
    "Library": "Biblioteca",
    "Refresh": "Actualizar",
    "Import": "Importar",
    "Add skeleton": "Agregar esqueleto",
    "Model3D folder": "Carpeta de Model3D",
    "The folder that holds data/library (the Model3D repository)": "La carpeta que contiene data/library (el repositorio de Model3D)",
    "Set the Model3D folder in the add-on preferences": "Configura la carpeta de Model3D en las preferencias del complemento",
    "Choose an image first": "Primero elige una imagen",
    "Open the Model3D desktop app": "Abre la aplicación de escritorio de Model3D",
    "The engine is still loading": "El motor todavía está cargando",
    "Checking the engine…": "Revisando el motor…",
    "Sending the image…": "Enviando la imagen…",
    "Sending the model…": "Enviando el modelo…",
    "Waiting in the queue…": "Esperando en la cola…",
    "Building the shape": "Construyendo la forma",
    "Painting the texture": "Pintando la textura",
    "Adding skeleton…": "Agregando esqueleto…",
    "Cancelling…": "Cancelando…",
    "Cancelled": "Cancelado",
    "Done": "Listo",
    "skeleton": "esqueleto",
    "Generate a 3D model from the image with the local engine": "Genera un modelo 3D a partir de la imagen con el motor local",
    "Cancel the running job": "Cancela el trabajo en curso",
    "Read the library again": "Vuelve a leer la biblioteca",
    "Import the selected library model into the scene": "Importa a la escena el modelo seleccionado de la biblioteca",
    "Add a skeleton to the selected library model": "Agrega un esqueleto al modelo seleccionado de la biblioteca",
}

TRANSLATIONS = {
    "es_ES": {(context, source): target for source, target in SPANISH.items() for context in ("*", "Operator")},
}
