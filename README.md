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
export LAB_PASS_MIKROTIK_LAB='tu_contraseña'
export LAB_PASS_CISCO_LAB='tu_contraseña'
```

## Ejecución

```bash
# Inventario + estado + reporte + R6 (lo-ssh-1)
python3 lab1_inventario.py

# Elegir otro número de lista N para lo-ssh-<N> / 10.253.0.<N>/32
python3 lab1_inventario.py --numero 7

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
- `RED_MIKROTIK` / `RED_CISCO`: parámetros de DHCP/NAT/rutas.
- `MARCA = "lab1-rg"`: prefijo para identificar y poder limpiar solo los objetos
  creados por este script.
- `NUMERO_DEF = 1`: valor por defecto de `--numero`.

---

## Notas de uso de IA

Parte de este código y de la depuración se apoyaron en un asistente de IA
(asistencia para redactar/documentar, revisar configuraciones de red e
interpretar salidas de depuración). Todo el contenido fue revisado, ejecutado y
validado contra los equipos reales del laboratorio por el/la estudiante, quien
asume la responsabilidad del resultado final.
