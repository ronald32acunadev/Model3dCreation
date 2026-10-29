# Especificación Técnica - Fase 1: Toolchain C++/CUDA y Entorno Base

**Proyecto:** Motor Local de Generación IA 3D  
**Entorno de Referencia:** Ubuntu 26.04.1 LTS | Dual NVIDIA RTX 5070 (Driver 595.99.02) | AMD Ryzen 7 5800X  
**Objetivo:** Instalación y validación del compilador nativo C++, entorno PyTorch y extensiones de CUDA  

---

## 1. Instalación de Dependencias del Sistema en Ubuntu 26.04

Ejecutar los siguientes comandos en la terminal de Ubuntu para instalar el compilador C++, los encabezados de desarrollo y las herramientas de construcción requeridas por las extensiones de PyTorch:



---

## 2. Creación y Configuración del Entorno Virtual Python

Se utilizará Python 3.11 para garantizar máxima compatibilidad con los binarios precompilados de PyTorch y CUDA 12.4+:

The virtual environment was not created successfully because ensurepip is not
available.  On Debian/Ubuntu systems, you need to install the python3-venv
package using the following command.

    apt install python3.11-venv

You may need to use sudo with that command.  After installing the python3-venv
package, recreate your virtual environment.

Failing command: /home/sandbox/3d-ai-studio/venv_3d_ai/bin/python3.11

Requirement already satisfied: pip in /usr/local/lib/python3.12/site-packages (24.2)
Requirement already satisfied: setuptools in /usr/local/lib/python3.12/site-packages (81.0.0)
Requirement already satisfied: wheel in /usr/local/lib/python3.12/site-packages (0.48.0)
Requirement already satisfied: packaging>=24.0 in /usr/local/lib/python3.12/site-packages (from wheel) (26.3)

---

## 3. Instalación de PyTorch 2.5+ con Soporte CUDA 12.x

Instalar la versión de PyTorch adecuada para la arquitectura de GPU NVIDIA RTX 5070:

Looking in indexes: PyTorch_CUDA124_Wheel_URL
Requirement already satisfied: torch in /usr/local/lib/python3.12/site-packages (2.11.0)
Requirement already satisfied: torchvision in /usr/local/lib/python3.12/site-packages (0.26.0)
Requirement already satisfied: torchaudio in /usr/local/lib/python3.12/site-packages (2.11.0)
Requirement already satisfied: filelock in /usr/local/lib/python3.12/site-packages (from torch) (3.32.3)
Requirement already satisfied: typing-extensions>=4.10.0 in /usr/local/lib/python3.12/site-packages (from torch) (4.16.0)
Requirement already satisfied: setuptools<82 in /usr/local/lib/python3.12/site-packages (from torch) (81.0.0)
Requirement already satisfied: sympy>=1.13.3 in /usr/local/lib/python3.12/site-packages (from torch) (1.14.0)
Requirement already satisfied: networkx>=2.5.1 in /usr/local/lib/python3.12/site-packages (from torch) (3.6.1)
Requirement already satisfied: jinja2 in /usr/local/lib/python3.12/site-packages (from torch) (3.1.6)
Requirement already satisfied: fsspec>=0.8.5 in /usr/local/lib/python3.12/site-packages (from torch) (2026.7.0)
Requirement already satisfied: cuda-toolkit==13.0.2 in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (13.0.2)
Requirement already satisfied: cuda-bindings<14,>=13.0.3 in /usr/local/lib/python3.12/site-packages (from torch) (13.3.1)
Requirement already satisfied: nvidia-cudnn-cu13==9.19.0.56 in /usr/local/lib/python3.12/site-packages (from torch) (9.19.0.56)
Requirement already satisfied: nvidia-cusparselt-cu13==0.8.0 in /usr/local/lib/python3.12/site-packages (from torch) (0.8.0)
Requirement already satisfied: nvidia-nccl-cu13==2.28.9 in /usr/local/lib/python3.12/site-packages (from torch) (2.28.9)
Requirement already satisfied: nvidia-nvshmem-cu13==3.4.5 in /usr/local/lib/python3.12/site-packages (from torch) (3.4.5)
Requirement already satisfied: triton==3.6.0 in /usr/local/lib/python3.12/site-packages (from torch) (3.6.0)
Requirement already satisfied: nvidia-cublas==13.1.0.3.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (13.1.0.3)
Requirement already satisfied: nvidia-cuda-runtime==13.0.96.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (13.0.96)
Requirement already satisfied: nvidia-cufft==12.0.0.61.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (12.0.0.61)
Requirement already satisfied: nvidia-cufile==1.15.1.6.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (1.15.1.6)
Requirement already satisfied: nvidia-cuda-cupti==13.0.85.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (13.0.85)
Requirement already satisfied: nvidia-curand==10.4.0.35.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (10.4.0.35)
Requirement already satisfied: nvidia-cusolver==12.0.4.66.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (12.0.4.66)
Requirement already satisfied: nvidia-cusparse==12.6.3.3.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (12.6.3.3)
Requirement already satisfied: nvidia-nvjitlink==13.0.88.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (13.0.88)
Requirement already satisfied: nvidia-cuda-nvrtc==13.0.88.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (13.0.88)
Requirement already satisfied: nvidia-nvtx==13.0.85.* in /usr/local/lib/python3.12/site-packages (from cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.2; platform_system == "Linux"->torch) (13.0.85)
Requirement already satisfied: numpy in /usr/local/lib/python3.12/site-packages (from torchvision) (2.4.3)
Requirement already satisfied: pillow!=8.3.*,>=5.3.0 in /usr/local/lib/python3.12/site-packages (from torchvision) (11.3.0)
Requirement already satisfied: cuda-pathfinder>=1.4.2 in /usr/local/lib/python3.12/site-packages (from cuda-bindings<14,>=13.0.3->torch) (1.6.1)
Requirement already satisfied: mpmath<1.4,>=1.1.0 in /usr/local/lib/python3.12/site-packages (from sympy>=1.13.3->torch) (1.3.0)
Requirement already satisfied: MarkupSafe>=2.0 in /usr/local/lib/python3.12/site-packages (from jinja2->torch) (3.0.3)

---

## 4. Compilación e Instalación de Extensiones C++/CUDA de Alto Rendimiento

Los modelos de IA 3D modernos requieren kernels de CUDA personalizados para operar con eficiencia. Ajustamos MAX_JOBS=8 para no saturar los 8 núcleos del AMD Ryzen 7 5800X.

Requirement already satisfied: trimesh in /usr/local/lib/python3.12/site-packages (4.11.4)

---

## 5. Script de Verificación del Entorno y Doble GPU (scripts/verify_gpus.py)

Guardar el siguiente código en scripts/verify_gpus.py para validar la correcta detección de hardware y librerías CUDA:



Para ejecutar la verificación:

