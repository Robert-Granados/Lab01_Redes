#!/usr/bin/env python3
"""Lab 1: inventario, estado y configuración segura con Netmiko."""
import os
import getpass
from datetime import datetime

import yaml
import paramiko
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoTimeoutException,
    NetmikoAuthenticationException,
)

# El Cisco IOS 3725 solo ofrece diffie-hellman-group1-sha1. Se agrega al final
# de la lista de preferencia: solo se usa si el equipo no acepta nada mejor.
# Requiere paramiko<4 (la 4.x eliminó este algoritmo).
if "diffie-hellman-group1-sha1" in paramiko.Transport._kex_info:
    paramiko.Transport._preferred_kex = tuple(
        paramiko.Transport._preferred_kex
    ) + ("diffie-hellman-group1-sha1",)

RUTA_INVENTARIO = "inventario.yaml"
CARPETA_LOGS = "logs"


def cargar_inventario(ruta=RUTA_INVENTARIO):
    with open(ruta) as f:
        return yaml.safe_load(f)["equipos"]


def obtener_password(nombre):
    """LAB_PASS_<NOMBRE> (mayúsculas, guiones como _); si no existe, getpass."""
    var = "LAB_PASS_" + nombre.upper().replace("-", "_")
    return os.environ.get(var) or getpass.getpass(f"Contraseña de {nombre}: ")


def conectar_y_probar(equipo):
    """Conecta a un equipo y devuelve un dict con el resultado."""
    resultado = {
        "nombre": equipo["nombre"],
        "host": equipo["host"],
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "estado": "ok",
    }
    dispositivo = {
        "device_type": equipo["device_type"],
        "host": equipo["host"],
        "username": equipo["usuario"],
        "password": obtener_password(equipo["nombre"]),
        "session_log": os.path.join(CARPETA_LOGS, f"{equipo['nombre']}.log"),
    }
    try:
        with ConnectHandler(**dispositivo) as conn:
            resultado["prompt"] = conn.find_prompt()
    except NetmikoTimeoutException as e:
        resultado["estado"] = f"error: timeout ({e})"
    except NetmikoAuthenticationException as e:
        resultado["estado"] = f"error: autenticación ({e})"
    except Exception as e:
        resultado["estado"] = f"error: {type(e).__name__}: {e}"
    return resultado


def main():
    os.makedirs(CARPETA_LOGS, exist_ok=True)
    for equipo in cargar_inventario():
        r = conectar_y_probar(equipo)
        print(f"{r['nombre']:<12} {r['host']:<16} {r['estado']}")


if __name__ == "__main__":
    main()
