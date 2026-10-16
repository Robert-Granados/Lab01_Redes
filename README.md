# Laboratorio 1 — Redes Programables (Netmiko)

Automatización de inventario, consulta de estado y configuración segura de
dispositivos de red con **Python + Netmiko**, según el enunciado
`03_Laboratorio_1_Netmiko_Enunciado.pdf`.

El script `lab1_inventario.py`:

1. Lee un inventario en YAML (R1).
2. Obtiene credenciales **solo** de variables de entorno o `getpass` (R2).
3. Conecta de forma robusta a cada equipo, con `session_log` por dispositivo (R3).
4. Recolecta 4 datos de estado con comandos por fabricante (R4).
5. Genera `reporte_estado.json` y una tabla por consola (R5).
6. Aplica un cambio **idempotente** en el equipo de escritura: una interfaz
   `lo-ssh-<N>` con IP `10.253.0.<N>/32` y comentario (R6).

Incluye además los **extras opcionales**: soporte **multivendor** (Cisco IOS en
el inventario) y consulta del **estado del MikroTik por su API REST** con
comparación SSH vs REST en el reporte (ver *Extra opcional*).

Adicionalmente, con `--red` configura DHCP, NAT e interconexión entre las dos
redes LAN del laboratorio.

---

## Topología

```
 PC2 --- ether1 [MikroTik] ether2 ==+
                                    |
                              switch 192.168.122.0/24 --- nube NAT (192.168.122.1) --- Internet
                                    |
 PC1 --- Fa0/0 [Cisco] Fa0/1 =======+
```

| Equipo     | Rol       | LAN                          | WAN                |
|------------|-----------|------------------------------|--------------------|
| MikroTik   | escritura | `ether1` → `10.60.30.202/24` | `ether2` (DHCP)    |
| Cisco IOS  | lectura*  | `Fa0/0` → `10.60.31.1/24`    | `Fa0/1` (DHCP)     |

- Cada PC tiene su propia subred: PC2 en `10.60.30.0/24`, PC1 en `10.60.31.0/24`.
- **El NAT del laboratorio lo hace el MikroTik** (`masquerade` por `ether2`).
  El Cisco solo enruta hacia Internet por el MikroTik.
  Se evita así el doble NAT Cisco↔nube de GNS3, que traducía el tráfico de PC1
  pero no devolvía las respuestas ICMP.
- Rutas estáticas: cada router conoce la LAN del otro por el switch.

> \* El Cisco figura como `rol: lectura` en el inventario. La configuración de
> red del Cisco solo se aplica con `--red`/`--limpiar`, nunca por el flujo
> normal de R6.

---

## Instalación

Requiere Python 3. Es necesario `paramiko<4` (la versión 4.x eliminó
`diffie-hellman-group1-sha1`, que el Cisco 3725 del laboratorio necesita).

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Credenciales (R2)

Las contraseñas **nunca** se guardan en el repositorio ni en el inventario. Se
leen de variables de entorno con el patrón `LAB_PASS_<NOMBRE_EQUIPO>` (nombre
del equipo en mayúsculas y con los guiones `-` convertidos en `_`):

| Equipo (inventario) | Variable de entorno       |
|---------------------|---------------------------|
| `mikrotik-lab`      | `LAB_PASS_MIKROTIK_LAB`   |
| `cisco-lab`         | `LAB_PASS_CISCO_LAB`      |

Si la variable no existe, el script la pide de forma interactiva con `getpass`
(no se muestra en pantalla).

```bash
export LAB_PASS_MIKROTIK_LAB='mi_contraseña'
export LAB_PASS_CISCO_LAB='mi_contraseña'
```

## Ejecución

```bash
# Inventario + estado + reporte + R6 (lo-ssh-1)
python3 lab1_inventario.py

# Además: DHCP + NAT + interconexión de red en ambos routers
python3 lab1_inventario.py --red

# Revertir SOLO los objetos creados por este script
python3 lab1_inventario.py --limpiar
```

### Opciones

| Opción       | Descripción                                                       |
|--------------|-------------------------------------------------------------------|
| `--numero N` | N para `lo-ssh-<N>` y `10.253.0.<N>/32` (por defecto `1`).        |
| `--red`      | Aplica además DHCP + NAT + interconexión (MikroTik y Cisco).      |
| `--limpiar`  | Elimina solo los objetos creados por el script.                   |

La ejecución de R6 es **idempotente**: la segunda vez informa `SIN CAMBIOS`.

Con `--red`, la configuración del **MikroTik** se guarda sola (RouterOS la
persiste al instante) y la del **Cisco** se guarda en `startup-config` (NVRAM),
por lo que **sobrevive a reinicios** del equipo. Las direcciones de las PC se
asignan por DHCP desde cada router.

---

## Estructura

| Archivo / carpeta        | Descripción                                              |
|--------------------------|----------------------------------------------------------|
| `lab1_inventario.py`     | Script principal (R1–R6 + configuración de red).         |
| `inventario.yaml`        | Inventario externo de equipos (R1).                      |
| `reporte_estado.json`    | Reporte de estado generado (R5).                         |
| `logs/`                  | `session_log` de Netmiko por dispositivo (R3).           |
| `requirements.txt`       | Dependencias.                                            |

### Variables principales del script

- `COMANDOS_POR_TIPO` / `PARSERS_POR_TIPO`: comandos y parseo por `device_type`
  (agregar un fabricante = agregar una entrada, sin tocar la lógica).
- `REST_POR_TIPO`: fabricantes con API REST y la ruta base de su API (extra).
- `RED_MIKROTIK` / `RED_CISCO`: parámetros de DHCP/NAT/rutas.
- `MARCA = "lab1-rg"`: prefijo para identificar y poder limpiar solo los objetos
  creados por este script.
- `NUMERO_DEF = 1`: valor por defecto de `--numero`.

---

## Extra opcional: multivendor + REST

### Multivendor (Cisco IOS)

El inventario ya incluye un equipo **Cisco IOS** (`cisco-lab`, `device_type:
cisco_ios`) junto al MikroTik. Como los comandos y el parseo están agrupados por
`device_type` (`COMANDOS_POR_TIPO` / `PARSERS_POR_TIPO`), el mismo flujo obtiene
los 4 datos de ambos fabricantes sin cambiar la lógica.

### API REST del MikroTik (comparación SSH vs REST)

Además de SSH, el script consulta el **MikroTik** por su **API REST** (librería
`requests`) y compara ambos resultados en el reporte, tal como muestra la columna
`REST` de la tabla (`= SSH`, `!= <campo>` o `-`).

| Dato             | SSH                        | REST                                       |
|------------------|----------------------------|--------------------------------------------|
| Nombre           | `/system identity print`   | `GET /rest/system/identity`                |
| Uptime           | `/system resource print`   | `GET /rest/system/resource`                |
| Interfaces / IP  | `/ip address print terse`  | `GET /rest/ip/address`                     |
| Ruta por defecto | `/ip route print terse`    | `GET /rest/ip/route?dst-address=0.0.0.0/0` |

En `reporte_estado.json`, cada equipo con REST agrega:

```json
"rest":        { "nombre": "...", "uptime": "...", "interfaces": [], "ruta_defecto": "..." },
"comparacion": { "coincide": true, "diferencias": [] }
```

El campo `uptime` se muestra en ambos pero **no** se compara, porque cambia entre
una lectura y la otra. Si el REST no está disponible, el reporte guarda el error
en `rest`/`comparacion` y el resto del laboratorio sigue funcionando (el extra no
rompe R1–R6).

**Requisito en el router** (una sola vez): el servicio **`www-ssl` (HTTPS)**
habilitado con un certificado y un usuario con la política `rest-api`. En
RouterOS 7.8 el REST se sirve **solo** por HTTPS (por HTTP devuelve 404):

```
/certificate add name=lab-ca common-name=lab-ca key-usage=key-cert-sign
/certificate sign lab-ca
/certificate add name=lab-web common-name=lab key-usage=tls-server
/certificate sign lab-web ca=lab-ca
/ip service set www-ssl certificate=lab-web disabled=no
```

El script usa el `host` del inventario y `verify=False` (el certificado del
laboratorio es autofirmado). `requests` y `urllib3` ya vienen en
`requirements.txt`.

---

## Notas de uso de IA

**Herramienta usada:** Opencode y Claude.

**Para qué se usó:**
- Diseñar la estructura general del script y su separación por requisitos
  (R1–R6).
- Redactar y ajustar los parsers de las salidas de RouterOS e IOS (expresiones
  regulares) y los comandos agrupados por `device_type`.
- Depurar la interconexión de red: doble NAT Cisco↔nube de GNS3, rutas estáticas
  y el orden de las reglas NAT del MikroTik.
- Implementar los extras: la consulta por **API REST** del MikroTik
  (`requests`), la comparación SSH vs REST y el soporte multivendor.
- Redactar y revisar este README.

**Qué NO se delegó (hecho por mi Robert Granados):**
- Decisiones de topología y direccionamiento del laboratorio.
- Ejecución y validación en los equipos reales: conexión SSH, pruebas de
  idempotencia (2.ª ejecución "SIN CAMBIOS"), `--limpiar`, pruebas de ping y
  verificación de que el resultado por REST coincide con el de SSH.

Todo el contenido fue revisado, ejecutado y validado contra los requisitos de este laboratorio por mi persona Robert Granados, quien asume la responsabilidad del
resultado final.