#!/usr/bin/env python3
"""Lab 1: inventario, estado y configuración segura con Netmiko."""
import os
import re
import json
import getpass
import argparse
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
RUTA_REPORTE = "reporte_estado.json"

# R4: comandos de consulta por fabricante. Agregar un fabricante nuevo es
# agregar una entrada aquí; la lógica del script no cambia.
# Claves: nombre, uptime, interfaces, ruta_defecto (los 4 datos de R4).
COMANDOS_POR_TIPO = {
    "mikrotik_routeros": {
        "nombre": "/system identity print",
        "uptime": "/system resource print",
        "interfaces": "/ip address print terse",
        "ruta_defecto": "/ip route print terse where dst-address=0.0.0.0/0",
    },
    "cisco_ios": {
        "nombre": "show running-config | include hostname",
        "uptime": "show version | include uptime",
        "interfaces": "show ip interface brief",
        "ruta_defecto": "show ip route 0.0.0.0",
    },
}


def _campo_routeros(texto, campo):
    """Valor de 'campo=valor' en una salida terse de RouterOS (o None)."""
    coincidencia = re.search(rf"\b{re.escape(campo)}=(\S+)", texto)
    return coincidencia.group(1) if coincidencia else None


def parsear_mikrotik(salidas):
    """Convierte las salidas crudas de RouterOS en un diccionario (R4)."""
    nombre = None
    for linea in salidas["nombre"].splitlines():
        if linea.strip().startswith("name:"):
            nombre = linea.split(":", 1)[1].strip()
            break

    uptime = None
    for linea in salidas["uptime"].splitlines():
        if linea.strip().startswith("uptime:"):
            uptime = linea.split(":", 1)[1].strip()
            break

    interfaces = []
    for linea in salidas["interfaces"].splitlines():
        direccion = _campo_routeros(linea, "address")
        interfaz = _campo_routeros(linea, "interface")
        if direccion and interfaz:
            interfaces.append({"interfaz": interfaz, "ip": direccion})

    ruta_defecto = _campo_routeros(salidas["ruta_defecto"], "gateway")
    return {
        "nombre": nombre,
        "uptime": uptime,
        "interfaces": interfaces,
        "ruta_defecto": ruta_defecto,
    }


def parsear_cisco(salidas):
    """Convierte las salidas crudas de Cisco IOS en un diccionario (R4)."""
    nombre = None
    for linea in salidas["nombre"].splitlines():
        partes = linea.split()
        if len(partes) >= 2 and partes[0] == "hostname":
            nombre = partes[1]
            break

    uptime = None
    if "uptime is" in salidas["uptime"]:
        uptime = salidas["uptime"].split("uptime is", 1)[1].strip()

    interfaces = []
    for linea in salidas["interfaces"].splitlines():
        partes = linea.split()
        # Filas: interfaz, IP, OK?, Method, Status, Protocol (Status puede
        # ocupar dos palabras: p. ej. "administratively down").
        if len(partes) >= 6 and partes[0] != "Interface":
            interfaces.append({
                "interfaz": partes[0],
                "ip": partes[1],
                "estado": " ".join(partes[4:-1]),
                "protocolo": partes[-1],
            })

    ruta_defecto = None
    for linea in salidas["ruta_defecto"].splitlines():
        linea = linea.strip()
        if linea.startswith("*"):
            ruta_defecto = linea.lstrip("* ").split()[0]
            break

    return {
        "nombre": nombre,
        "uptime": uptime,
        "interfaces": interfaces,
        "ruta_defecto": ruta_defecto,
    }


# Parser por fabricante, igual que COMANDOS_POR_TIPO.
PARSERS_POR_TIPO = {
    "mikrotik_routeros": parsear_mikrotik,
    "cisco_ios": parsear_cisco,
}

# Etiqueta (comment) de TODO lo que crea este script, para poder hacer --limpiar
# sin tocar objetos de otros estudiantes.
MARCA = "lab1-rg"

# R6: número de lista para lo-ssh-<N> / 10.253.0.<N>/32 (configurable con --numero).
NUMERO_DEF = 1

# --- Configuración de red del laboratorio (DHCP + NAT + interconexión) ---
# Topología: PC2 -- ether1 [MT] ether2 == switch 192.168.122.0/24 == Fa0/1 [Cisco] Fa0/0 -- PC1
# Cada PC en su propia subred; los routers se enrutan entre sí por la red del switch.
DNS_CLIENTES = "192.168.122.1"

RED_MIKROTIK = {
    "lan": "ether1",
    "wan": "ether2",
    "ip_cidr": "10.60.30.202/24",
    "red": "10.60.30.0/24",
    "gateway": "10.60.30.202",
    "pool_nombre": "pool-lab1",
    "pool_rango": "10.60.30.100-10.60.30.200",
    "dhcp_nombre": "dhcp-lab1",
    "ruta_peer": "10.60.31.0/24",  # LAN del Cisco
}

RED_CISCO = {
    "lan": "FastEthernet0/0",
    "wan": "FastEthernet0/1",
    "ip": "10.60.31.1",
    "mascara": "255.255.255.0",
    "red": "10.60.31.0",
    "gateway": "10.60.31.1",
    "pool_nombre": "POOL-LAB1",
    "acl": "10",             # ACL de un NAT previo: solo para limpieza/migración
    "acl_nat": "NAT-LAB1",   # ACL extendida de un NAT previo: solo limpieza
    "ruta_peer": "10.60.30.0",  # LAN del MikroTik
    "ruta_peer_mascara": "255.255.255.0",
}

# Caché de contraseñas (para no pedirla dos veces por equipo).
_PASSWORDS = {}


def cargar_inventario(ruta=RUTA_INVENTARIO):
    with open(ruta) as f:
        return yaml.safe_load(f)["equipos"]


def obtener_password(nombre):
    """LAB_PASS_<NOMBRE> (mayúsculas, guiones como _); si no existe, getpass.

    Se guarda en caché para no volver a pedirla si se reconecta al mismo equipo.
    """
    if nombre in _PASSWORDS:
        return _PASSWORDS[nombre]
    var = "LAB_PASS_" + nombre.upper().replace("-", "_")
    clave = os.environ.get(var) or getpass.getpass(f"Contraseña de {nombre}: ")
    _PASSWORDS[nombre] = clave
    return clave


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
            # R4: consultar cada comando y convertir la salida a diccionario.
            comandos = COMANDOS_POR_TIPO[equipo["device_type"]]
            salidas = {
                dato: conn.send_command(comando, read_timeout=30)
                for dato, comando in comandos.items()
            }
            resultado["datos"] = PARSERS_POR_TIPO[equipo["device_type"]](salidas)
    except NetmikoTimeoutException as e:
        resultado["estado"] = f"error: timeout ({e})"
    except NetmikoAuthenticationException as e:
        resultado["estado"] = f"error: autenticación ({e})"
    except Exception as e:
        resultado["estado"] = f"error: {type(e).__name__}: {e}"
    return resultado


def guardar_reporte(resultados, ruta=RUTA_REPORTE):
    """R5: escribe la lista de resultados (datos + fecha/hora + estado) en JSON."""
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False)


def _resumen_equipo(resultado):
    """Devuelve (cantidad de interfaces, gateway por defecto) o guiones si falló."""
    datos = resultado.get("datos")
    if not datos:
        return "-", "-"
    cantidad = len(datos.get("interfaces", []))
    gateway = datos.get("ruta_defecto") or "-"
    return cantidad, gateway


def imprimir_tabla(resultados):
    """R5: resumen en consola (equipo, IP, estado, #interfaces, gateway)."""
    encabezado = (
        f"{'EQUIPO':<14} {'IP':<16} {'ESTADO':<30} {'IFACES':>6}  {'GATEWAY':<16}"
    )
    print(encabezado)
    print("-" * len(encabezado))
    for r in resultados:
        cantidad, gateway = _resumen_equipo(r)
        estado = r["estado"]
        if len(estado) > 30:  # recorta mensajes de error largos para la tabla
            estado = estado[:27] + "..."
        print(
            f"{r['nombre']:<14} {r['host']:<16} {estado:<30} "
            f"{str(cantidad):>6}  {gateway:<16}"
        )


def _dispositivo(equipo):
    """Dict de conexión Netmiko para un equipo del inventario."""
    return {
        "device_type": equipo["device_type"],
        "host": equipo["host"],
        "username": equipo["usuario"],
        "password": obtener_password(equipo["nombre"]),
        "session_log": os.path.join(CARPETA_LOGS, f"{equipo['nombre']}.log"),
    }


# --------------------------------------------------------------------------
# R6 + configuración de red (solo lectura/escritura según corresponda)
# --------------------------------------------------------------------------
def _routeros_cuenta(conn, ruta, filtro):
    """Cuántos objetos de 'ruta' cumplen 'filtro' (0 = no existe)."""
    salida = conn.send_command(f":put [:len [{ruta} find where {filtro}]]")
    try:
        return int(salida.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def _routeros_agregar_si_falta(conn, descripcion, ruta, filtro, comando):
    """Crea en RouterOS solo si no existe. Devuelve True si creó algo."""
    if _routeros_cuenta(conn, ruta, filtro) > 0:
        print(f"  [=] {descripcion}: ya existe")
        return False
    salida = conn.send_command(comando)
    if "failure" in salida.lower() or "error" in salida.lower():
        raise RuntimeError(f"{descripcion}: {salida.strip()}")
    print(f"  [+] {descripcion}: creado")
    return True


def configurar_mikrotik(conn, numero, peer, con_red=True):
    """R6 (+ red si con_red): asegura lo-ssh-<N> y, si aplica, DHCP/NAT/ruta.

    Idempotente: consulta antes de crear. Devuelve True si hubo cambios.
    """
    nombre_lo = f"lo-ssh-{numero}"
    ip_lo = f"10.253.0.{numero}/32"
    cambios = []

    # --- R6: bridge sin puertos + IP /32 con comentario ---
    cambios.append(_routeros_agregar_si_falta(
        conn, f"bridge {nombre_lo}", "/interface bridge",
        f'name="{nombre_lo}"',
        f'/interface bridge add name={nombre_lo} comment="{nombre_lo}"'))
    cambios.append(_routeros_agregar_si_falta(
        conn, f"IP {ip_lo}", "/ip address", f'address="{ip_lo}"',
        f'/ip address add address={ip_lo} interface={nombre_lo} comment="{nombre_lo}"'))

    if con_red:
        red = RED_MIKROTIK
        cambios.append(_routeros_agregar_si_falta(
            conn, f"IP LAN {red['ip_cidr']}", "/ip address",
            f'address="{red["ip_cidr"]}"',
            f'/ip address add address={red["ip_cidr"]} interface={red["lan"]} '
            f'comment="{MARCA}"'))
        cambios.append(_routeros_agregar_si_falta(
            conn, f"pool DHCP {red['pool_nombre']}", "/ip pool",
            f'name="{red["pool_nombre"]}"',
            f'/ip pool add name={red["pool_nombre"]} ranges={red["pool_rango"]} '
            f'comment="{MARCA}"'))
        cambios.append(_routeros_agregar_si_falta(
            conn, f"red DHCP {red['red']}", "/ip dhcp-server network",
            f'address="{red["red"]}"',
            f'/ip dhcp-server network add address={red["red"]} '
            f'gateway={red["gateway"]} dns-server={red["gateway"]} comment="{MARCA}"'))
        cambios.append(_routeros_agregar_si_falta(
            conn, f"servidor DHCP {red['dhcp_nombre']}", "/ip dhcp-server",
            f'name="{red["dhcp_nombre"]}"',
            f'/ip dhcp-server add name={red["dhcp_nombre"]} interface={red["lan"]} '
            f'address-pool={red["pool_nombre"]} disabled=no comment="{MARCA}"'))
        cambios.append(_routeros_agregar_si_falta(
            conn, "NAT masquerade", "/ip firewall nat",
            f'chain=srcnat action=masquerade out-interface="{red["wan"]}"',
            f'/ip firewall nat add chain=srcnat action=masquerade '
            f'out-interface={red["wan"]} comment="{MARCA}"'))
        # Excepción: NO traducir el tráfico entre las dos LANs (PC<->PC).
        # Debe quedar ANTES de la regla masquerade.
        if _routeros_cuenta(conn, "/ip firewall nat",
                            f'chain=srcnat action=accept '
                            f'dst-address="{red["ruta_peer"]}"') == 0:
            conn.send_command(
                f'/ip firewall nat add chain=srcnat action=accept '
                f'src-address={red["red"]} dst-address={red["ruta_peer"]} '
                f'comment="{MARCA}-lan"')
            conn.send_command(
                f'/ip firewall nat move [find where comment="{MARCA}-lan"] '
                f'destination=[find where comment="{MARCA}"]')
            print("  [+] excepción NAT LAN-vecina: creada")
            cambios.append(True)
        else:
            print("  [=] excepción NAT LAN-vecina: ya existe")
            cambios.append(False)
        if _routeros_cuenta(conn, "/ip route", f'dst-address="{red["ruta_peer"]}"') == 0:
            conn.send_command(
                f'/ip route add dst-address={red["ruta_peer"]} gateway={peer} '
                f'comment="{MARCA}"')
            print(f"  [+] ruta a {red['ruta_peer']} vía {peer}: creada")
            cambios.append(True)
        else:
            print(f"  [=] ruta a {red['ruta_peer']}: ya existe")
            cambios.append(False)
        conn.send_command("/ip dns set allow-remote-requests=yes")
        # Sin redirecciones ICMP: el Cisco enruta a internet por este router,
        # y una redirección haría que el Cisco saltara el NAT del MikroTik.
        conn.send_command("/ip settings set send-redirects=no")

    # Verificación posterior con comando de lectura.
    print("  [verificación]")
    for cmd in (f'/ip address print terse where address="{ip_lo}"',
                f'/ip dhcp-server print terse where name="{RED_MIKROTIK["dhcp_nombre"]}"'
                if con_red else None):
        if cmd:
            print("    " + " ".join(conn.send_command(cmd).split()))

    return any(cambios)


def limpiar_mikrotik(conn, numero):
    """--limpiar: elimina SOLO los objetos de este script en RouterOS."""
    nombre_lo = f"lo-ssh-{numero}"
    ip_lo = f"10.253.0.{numero}/32"
    red = RED_MIKROTIK
    acciones = [
        ("IP lo-ssh", f'/ip address remove [find where address="{ip_lo}"]'),
        ("bridge lo-ssh", f'/interface bridge remove [find where name="{nombre_lo}"]'),
        ("servidores DHCP", f'/ip dhcp-server remove [find where comment="{MARCA}"]'),
        ("redes DHCP", f'/ip dhcp-server network remove [find where comment="{MARCA}"]'),
        ("pools DHCP", f'/ip pool remove [find where comment="{MARCA}"]'),
        ("NAT", f'/ip firewall nat remove [find where comment="{MARCA}"]'),
        ("rutas", f'/ip route remove [find where comment="{MARCA}"]'),
        ("direcciones IP", f'/ip address remove [find where comment="{MARCA}"]'),
    ]
    for descripcion, cmd in acciones:
        conn.send_command(cmd)
        print(f"  [-] {descripcion}: eliminado (si existía)")


def configurar_cisco(conn, peer):
    """DHCP + rutas en Cisco IOS.

    El NAT del laboratorio lo hace el MikroTik: el Cisco enruta a internet
    por el MikroTik (peer). Se evita el doble NAT Cisco<->nube de GNS3, que
    traducía pero no devolvía las respuestas ICMP de PC1. Idempotente.
    """
    red = RED_CISCO
    comandos = [
        f"interface {red['lan']}",
        f"ip address {red['ip']} {red['mascara']}",
        "no shutdown",
        "no ip nat inside",
        "exit",
        f"interface {red['wan']}",
        "no ip nat outside",
        "exit",
        # Migración: quitar un NAT previo del Cisco si estuviera configurado.
        f"no ip nat inside source list {red['acl_nat']} interface {red['wan']} overload",
        f"no ip nat inside source list {red['acl']} interface {red['wan']} overload",
        f"no ip access-list extended {red['acl_nat']}",
        f"no access-list {red['acl']}",
        f"ip dhcp pool {red['pool_nombre']}",
        f"network {red['red']} {red['mascara']}",
        f"default-router {red['gateway']}",
        f"dns-server {DNS_CLIENTES}",
        "exit",
        # Ruta hacia la LAN del MikroTik y ruta por defecto por el MikroTik.
        f"ip route {red['ruta_peer']} {red['ruta_peer_mascara']} {peer}",
        f"ip route 0.0.0.0 0.0.0.0 {peer}",
    ]
    conn.send_config_set(comandos)
    print("  [+] DHCP + rutas aplicados (NAT delegado al MikroTik)")
    print("  [verificación]")
    salida = conn.send_command(f"show ip dhcp pool {red['pool_nombre']}")
    lineas = [l for l in salida.splitlines() if l.strip()]
    print("    " + (lineas[0] if lineas else "(sin salida)"))
    print("    " + conn.send_command(
        "show ip route | include 0.0.0.0|10.60.30.0").splitlines()[0])


def limpiar_cisco(conn, peer):
    """--limpiar: revierte la configuración de red en Cisco IOS."""
    red = RED_CISCO
    comandos = [
        f"no ip route 0.0.0.0 0.0.0.0 {peer}",
        f"no ip nat inside source list {red['acl_nat']} interface {red['wan']} overload",
        f"no ip nat inside source list {red['acl']} interface {red['wan']} overload",
        f"interface {red['lan']}",
        "no ip nat inside",
        "exit",
        f"interface {red['wan']}",
        "no ip nat outside",
        "exit",
        f"no ip access-list extended {red['acl_nat']}",
        f"no access-list {red['acl']}",
        f"no ip route {red['ruta_peer']} {red['ruta_peer_mascara']} {peer}",
        f"no ip dhcp pool {red['pool_nombre']}",
        f"interface {red['lan']}",
        "no ip address",
        "shutdown",
    ]
    conn.send_config_set(comandos)
    print("  [-] configuración de red revertida")


def aplicar_configuracion(equipo, numero, limpiar, con_red):
    """Conecta a un equipo y aplica/limpia su configuración. Errores por equipo."""
    print(f"\n[*] Configurando {equipo['nombre']} ({equipo['rol']})...")
    try:
        with ConnectHandler(**_dispositivo(equipo)) as conn:
            if equipo["device_type"] == "mikrotik_routeros":
                if limpiar:
                    limpiar_mikrotik(conn, numero)
                else:
                    cambios = configurar_mikrotik(conn, numero, peer=equipo["peer"],
                                                  con_red=con_red)
                    print("  [OK] " + ("cambios aplicados" if cambios
                                       else "SIN CAMBIOS (idempotente)"))
            elif equipo["device_type"] == "cisco_ios":
                if limpiar:
                    limpiar_cisco(conn, peer=equipo["peer"])
                else:
                    configurar_cisco(conn, peer=equipo["peer"])
    except NetmikoTimeoutException as e:
        print(f"  [ERROR] timeout: {e}")
    except NetmikoAuthenticationException as e:
        print(f"  [ERROR] autenticación: {e}")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")


def _agregar_peer(equipos):
    """Agrega a cada equipo la IP del OTRO router (peer) para las rutas estáticas."""
    por_tipo = {e["device_type"]: e["host"] for e in equipos}
    for e in equipos:
        if e["device_type"] == "mikrotik_routeros":
            e["peer"] = por_tipo.get("cisco_ios")
        elif e["device_type"] == "cisco_ios":
            e["peer"] = por_tipo.get("mikrotik_routeros")
    return equipos


def main():
    parser = argparse.ArgumentParser(
        description="Lab 1: inventario, estado y configuración segura con Netmiko")
    parser.add_argument("--numero", type=int, default=NUMERO_DEF,
                        help=f"N para lo-ssh-<N> / 10.253.0.<N>/32 (def. {NUMERO_DEF})")
    parser.add_argument("--red", action="store_true",
                        help="aplica además DHCP + NAT + interconexión (MikroTik y Cisco)")
    parser.add_argument("--limpiar", action="store_true",
                        help="elimina SOLO los objetos creados por este script")
    args = parser.parse_args()

    os.makedirs(CARPETA_LOGS, exist_ok=True)
    equipos = _agregar_peer(cargar_inventario())
    resultados = [conectar_y_probar(equipo) for equipo in equipos]
    guardar_reporte(resultados)
    imprimir_tabla(resultados)
    print(f"\nReporte guardado en {RUTA_REPORTE}")

    # R6: solo equipos con rol de escritura.
    for equipo in equipos:
        if equipo.get("rol") != "escritura":
            continue
        aplicar_configuracion(equipo, args.numero, args.limpiar, args.red)

    # Con --red, configurar también el resto (p. ej. el Cisco, de solo lectura).
    if args.red or args.limpiar:
        for equipo in equipos:
            if equipo.get("rol") == "escritura":
                continue
            aplicar_configuracion(equipo, args.numero, args.limpiar, args.red)


if __name__ == "__main__":
    main()
