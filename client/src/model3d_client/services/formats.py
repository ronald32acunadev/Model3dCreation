"""The export formats the app offers, and what each one keeps of a model."""

FORMATS = {
    ".glb": "glTF binary",
    ".fbx": "FBX",
    ".obj": "Wavefront OBJ",
    ".stl": "STL",
    ".usdz": "USDZ",
}
CONVERTED = frozenset({".fbx", ".obj", ".stl", ".usdz"})
KEEPS_SKELETON = frozenset({".glb", ".fbx", ".usdz"})
KEEPS_TEXTURE = frozenset({".glb", ".fbx", ".obj", ".usdz"})
