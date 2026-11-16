# X6 Manager

Manager para el mouse **Attack Shark X6** (USB `1d57:fa60`): DPI onboard, perfiles,
bandeja del sistema, UI Qt y **capas tipo G-Shift** por software.

La memoria del mouse **no es legible** (`GET_REPORT` devuelve STALL), asi que el estado
(perfil activo, stage de DPI) se guarda en disco y se re-aplica al arrancar.

```
app/                 daemon, tray, UI, entrypoint
x6core/              protocolo HID, transporte, perfiles, capas, acciones
x6core/backends/     captura de botones (pynput + hook Win32)
examples/            perfil de ejemplo con G-Shift
tools/               escritura/verificacion en hardware (write_config, watchers)
tests/               tests de protocolo, perfiles y motor de capas
```

## Instalacion

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Linux: hace falta acceso al hidraw del dongle (regla udev con `TAG+="uaccess"` o
`MODE="0660"` para el grupo `input`). La interfaz del canal vendor es la **2**.

## Uso

```bash
python -m app                  # bandeja del sistema
python -m app --ui             # ventana Qt (necesita PySide6)
python -m app --dry-run        # no escribe en el mouse ni inyecta teclas
python -m app --no-host        # solo perfiles onboard, sin capturar botones
python -m app --profile FPS    # perfil inicial
python -m app --no-hotkeys     # sin Ctrl+Alt+1..9
python -m app --swap-side-buttons   # si atras/adelante estan invertidos
```

Configuracion: `%LOCALAPPDATA%\x6-manager` (Windows) o `~/.config/x6-manager` (Linux),
con `profiles/*.json` y `state.json` (ultimo perfil activo).

Crea perfiles copiando un JSON de `examples/` a `profiles/`. Se validan al cargar: DPI
50-26000 multiplo de 50, maximo 8 stages, colores `0xRRGGBB`, etc.

## Protocolo (medido y capturado en hardware real)

Verificado el 2026-11-14 sobre un dongle `1d57:fa60` (firmware 0x0113) con dos fuentes
independientes: lectura pasiva del dongle y captura de los `HidD_SetFeature` del software
oficial (ver `tools/hidshim/`), con checksums comprobados byte a byte.

### Dos colecciones distintas en la interfaz 2

| Coleccion | Direccion | Uso |
|---|---|---|
| `usage_page 0x0A` (Col03) | entrada | heartbeat, ACK y avisos de DPI |
| `usage_page 0x0B` (Col04) | salida | feature reports de configuracion |

Escribir en `0x0A` devuelve `-1` en Windows. El software oficial escribe con
`HidD_SetFeature` sobre `0x0B`; en Linux vale hidraw/hidapi sobre la misma interfaz.

**La memoria del mouse es solo de escritura**: `GET_FEATURE` de `0x01..0x0B` da STALL en
las cuatro colecciones. No se puede leer la config: hay que guardar copia local.

### Reports de escritura (feature, primer byte = report ID)

| RID | Bytes | Contenido |
|-----|-------|-----------|
| 0x04 | 56 | DPI stages, colores, stage activo, flags |
| 0x05 | 15 | luces, sleep, debounce |
| 0x06 | 9 | polling rate |
| 0x08 | 59 | mapa de botones: 18 entradas de 3 bytes |
| 0x09 | 64 | macros onboard: 3 chunks (ver abajo) |
| 0x0C | 10 | **desconocido: no enviar** |

El firmware valida la longitud de cada report; por eso `send()` llama a
`validate_report()` antes de tocar el hardware. Ojo con `0x09`: **los tres chunks
viajan en buffers de 64 bytes**, aunque el ultimo solo lleve 12 bytes utiles; con
menos, `HidD_SetFeature` devuelve `ERROR_INVALID_PARAMETER`.

### Checksums

| Report | Algoritmo | Posicion |
|---|---|---|
| 0x04 | suma de `payload[3:50]` en big-endian | `[50:52]` |
| 0x08 | suma de `payload[3:57] & 0xFF` | `[58]` |
| 0x06 | complemento a uno | byte final |

### Entrada: `0x03` (5 bytes)

```
03 10 40 01 0a      # heartbeat: byte2=0x40, byte3=stage activo, byte4*10 = bateria %
03 10 10 03 0a      # boton DPI fisico: byte3 = stage nuevo
03 10 50 01 08      # ACK: byte3 = perfil, byte4 = report ID aceptado
```

El heartbeat llega cada ~2.0 s. **El dongle solo ACKea los reports que cambian algo**:
reenviar la config ya guardada se acepta pero no produce ACK, asi que la falta de ACK no
es un error (por eso `apply_config` no aborta).

Payload `0x04`: `[0]=0x04 [1]=0x38 [2]=perfil [5]=mascara de stages`, lows en `[8..15]`,
highs en `[16..23]`, `[24]=stage_activo+1`, colores **BGR** en `[25..48]`, `[49]=0x01`.
DPI en el alambre: `dpi/50 - 1` en 16 bits LE.

### Botones: indice en el report `0x08`

Cada entrada es una terna `(codigo, arg1, arg2)`. Las teclas usan `0x11 <mods> <usage>`
con modificadores ctrl=0x01, shift=0x02, alt=0x04, win=0x08.

| Indice | Boton fisico | Valor de fabrica |
|---|---|---|
| 0 / 1 / 2 | izquierdo / derecho / rueda | `02` / `03` / `04` |
| 3 | celda de configuracion (sin boton en el X6) | `0d` (ciclo de DPI) |
| 4 / 5 | **los dos de arriba** (ex DPI+ / DPI−) | `1b` / `1c` (volumen) |
| 6 / 7 | lateral delantero / trasero | `06` / `05` |
| 8 | sin documentar | `3c` |
| 9..15 | libres (sin boton fisico) | `01` (off) |
| 16 / 17 | rueda abajo / arriba | `0a` / `09` |

El boton DPI de detras de la rueda **no genera evento**: lo gestiona el firmware y solo
avisa por el canal vendor (`03 10 10`), sin release.

### Macros onboard (`0x09`) — verificado en hardware

El macro vive en el raton y se ejecuta **sin ningun software en el PC**. Es lo mas
parecido a "configuracion que viaja con el mouse".

Prueba completa: macro `abc` en el slot 7, boton delantero mapeado a ese macro con
`0x08 = 12 00 07`, y al pulsar el lateral aparecio `abc` en el Bloc de notas con el
daemon parado.

Buffer logico de 131 bytes, enviado en 3 chunks:

```
L[0]=09  L[1]=0x83 (longitud logica)  L[2]=slot (indice de boton + 1)
L[3..6]   cabecera (campos de la estructura de macro de la app; 0 sirve)
L[7]      modo de reproduccion (minimo 1)
L[8..27]  0
L[28]     numero de items de 2 bytes
L[29..128] items
L[129..130] suma de L[3..128] en big-endian
```

En el cable cada chunk es `09 <len> <slot> <chunk#> + datos`:

| chunk | `len` | datos |
|---|---|---|
| 0 | `0x40` | 60 B de `L[3..62]` |
| 1 | `0x40` | 60 B de `L[62..121]` |
| 2 | `0x0C` | 8 B de `L[123..130]` (6 de datos + checksum), resto a cero |

Item de un evento `(tecla, pulsada, retardo_ms)`:

- retardo ≤ 1270 ms: `[round(delay/10) | flag, usage]`, con `flag = 0x01` al pulsar y
  `0x81` al soltar (el bit 7 es "suelta").
- retardo > 1270 ms: ese byte solo tiene 7 bits, asi que el evento lleva el resto y se
  anade un item extra `[delay/200, 0x03]`.

`usage` es el HID keyboard usage (el mismo de `KEY_USAGE`). Caben 50 items de 2 bytes.

Asignacion al boton: la entrada del `0x08` es `(0x12, 0x00, slot)`.

```
py tools/write_config.py --macro-slot 7 --macro-text "abc" \
    --map-macro forward --yes       # escribe el macro y mapea el lateral delantero
py tools/write_config.py --set forward=forward --yes    # deshacer el mapeo
```

Los macros se guardan tambien en `onboard.json` (no se pueden leer del raton). El
`0x09` no pasa por `apply_config` porque los 3 chunks comparten report ID: usa
`Dongle.apply_macro()`.

### Limite del firmware: que teclas sabe emitir

El teclado del dongle (interfaz 3) no es un teclado completo: su descriptor HID es un
**mapa de bits limitado a usages `0x00..0x2F`**

```
05 07 19 e0 29 e7 ...            # modificadores (ctrl/shift/alt/win)
19 00 29 2f 15 00 25 01 ...      # solo usages 0x00-0x2F
```

Es decir, **letras, digitos y puntuacion basica** (`- = [ ] ; ' , . /`, Enter, Esc,
Backspace, Tab, Space). Todo lo que quede fuera de ese rango **nunca llega al SO**,
aunque el report `0x08` lo ACKe y el software oficial lo liste:

- F1-F12 (`0x3A..0x45`), Scroll Lock (`0x47`), Pause, Insert, flechas, teclado
  numerico y multimedia **no se emiten**.
- F13-F24 tampoco existen en la lista oficial: escribir `0x68`/`0x69` deja el boton
  mudo.

Verificado en hardware: `a` y `b` (`0x11 0x00 0x04` / `0x11 0x00 0x05`) llegan y se
escriben; `F8`, `F9`, `F13` y `Scroll Lock` no llegan.

Los modificadores **si** funcionan: `Ctrl+C`/`Ctrl+V` (`0x11 0x01 ...`) llegan como
`ctrl_l` + `'\x03'` / `'\x16'`. Pero un combo `Ctrl+Alt+<tecla>` se interpreta como
**AltGr**: Windows entrega `ctrl` + `alt` y **ningun caracter**, y los modificadores se
quedan pulsados, lo que alteraria los clics siguientes. Por eso no sirve como disparador.

**Conclusion practica**: para G-Shift usa un **boton lateral** (`forward`/`backward`),
que es lo que hace Logitech. El teclado del dongle queda para pruebas o para quien no
use esas teclas al escribir: si pones `shift_button: "9"`, el hook bloquea tambien el `9`
del teclado real, porque no puede distinguir el origen. Probado en hardware: con
`shift_button: "9"` la pulsacion activa la capa y **no** llega ningun caracter a la
ventana activa.

### Botones: que manda cada uno (X6 con firmware 0x0113)

| Boton | Evento |
|---|---|
| Izquierdo / derecho / rueda | botones estandar (`left`/`right`/`middle`) |
| Los 2 laterales | **XBUTTON1 / XBUTTON2** (`x1` = atras, `x2` = adelante) |
| Los 2 de arriba | lo asignado en memoria onboard (report `0x08`) |
| Boton DPI (detras de la rueda) | **nada**: solo `03 10 10` por el canal vendor |


## Capas tipo G-Shift (G-Alt)

Si, es posible, y es la razon de ser del modo host. Logitech lo hace en firmware; aca
lo hace el daemon porque el remap onboard (0x07) todavia no esta capturado.

```json
{
  "shift_button": "forward",
  "shift_layer": "shift",
  "shift_mode": "hold",
  "layers": {
    "shift": {
      "backward": "ctrl+c",
      "middle": { "kind": "media", "value": "mute", "macro_id": "" },
      "dpi": "dpi_cycle"
    }
  }
}
```

Con eso: mantener *adelante* pulsado y tocar *atras* manda `Ctrl+C`; al soltar, todo
vuelve a la normalidad. `shift_mode: "toggle"` alterna la capa con cada pulsacion (util
para el boton DPI, que por el canal vendor no entrega evento de release).

El perfil verificado en hardware usa el lateral trasero como disparador (lo habitual en
Logitech: el pulgar mantiene el shift y el resto de botones cambian):

```json
{
  "shift_button": "backward",
  "shift_layer": "shift",
  "shift_mode": "hold",
  "layers": {
    "shift": {
      "forward": "ctrl+c",
      "middle": { "kind": "media", "value": "mute", "macro_id": "" }
    }
  }
}
```

Comprobado de extremo a extremo: `backward` DOWN activa la capa, `forward` en la capa
inyecta `Ctrl+C` y el evento original queda bloqueado (el navegador no retrocede), y sin
la capa los botones pasan sin supresion ni latencia.

Acciones disponibles (`kind`): `click` (left/right/middle/forward/backward), `shortcut`
(tap, ej. `ctrl+alt+1`), `hold` (down al pulsar, up al soltar; ideal para push-to-talk),
`macro`, `media` (play_pause/next/previous/volume_up/volume_down/mute), `dpi_cycle`,
`dpi_up`, `dpi_down`, `off`.

### Limites reales

- **Suprimir el evento original** solo es posible en Windows (hook `WH_MOUSE_LL` via
  `pynput.win32_event_filter`). En Linux/X11 pynput solo observa: el boton hara su accion
  nativa **y** la de la capa. Para paridad en Linux hace falta `evdev` con `grab` + `uinput`.
- El hook corre en el hilo del SO: por eso las acciones se encolan en un hilo aparte.
  Bloquearlo con un SET_REPORT de 3s haria que Windows descarte el hook.
- Un boton reasignado a si mismo pasa sin tocar (cero latencia). El resto suma el
  round-trip del hook (~1 ms).
- El estado de capa es local al daemon: si el juego corre como administrador y el daemon
  no, Windows no entrega los eventos (hook de integridad).

## Tests

```bash
python -m unittest discover -s tests -v
```

81 tests sin hardware: layout del payload 0x04, checksum, round-trip DPI, parseo de
`0x03`, validacion/persistencia de perfiles (incluye migracion del formato viejo),
maquina de estados de capas (hold, toggle, release diferido, reset) y disparadores de
teclado del rango `0x00-0x2F`.

Verificacion con hardware real (herramientas en `tools/`):

```bash
python tools/write_config.py --set dpi_up=9 --set dpi_down=0 --yes   # escribe 0x08 con ACK
python tools/watch_keys.py --seconds 60      # que manda cada boton al SO
python tools/watch_status.py --seconds 60    # heartbeats y ACKs del dongle
python tools/gshift_demo.py --profile gshift-side --seconds 120
python tools/mute_state.py --seconds 60      # prueba objetiva de media:mute
```

## Pendiente

- `0x07` (remap onboard): sin capturar; permitiria reasignar botones sin PC.
- Macros onboard: **ya funciona** (ver arriba); falta probar si el teclado del dongle
  limita tambien su reproduccion igual que el `0x08`.
- `0x06` (checksum del polling: el corto usa complemento a uno y no coincide con el
  aditivo del 0x04), `0x05` (sleep).
- LOD/debounce/angle-snap/motion-sync: hoy solo se guardan, no se envian.
- Auto-switch por app (`auto_apps`), autostart en Windows, backend `evdev` para Linux.
- Iconos por estado y modo single-instance.
