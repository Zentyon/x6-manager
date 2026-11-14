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

## Protocolo (lo que se sabe)

Todo se escribe por `SET_REPORT` feature `0x0304` en la interfaz 2. Report IDs:

| RID | Dir | Bytes | Contenido |
|-----|-----|-------|-----------|
| 0x03 | in | 4 | `[03 EV SUB BAT]`: `0x11` heartbeat+bateria, `0x10` boton DPI, `0x50`/`0x12`/`0x21` ACK |
| 0x04 | feature | 52 | DPI stages, colores, stage activo, flags |
| 0x05 | feature | 12 | sleep (sin capturar) |
| 0x06 | feature | 8/9 | polling (sin validar) |
| 0x07 | feature | 7 | remap de botones onboard (sin capturar) |
| 0x09 | feature | 63 | macros onboard (sin capturar) |

Payload 0x04: `[0]=0x04 [1]=0x38 [2]=0x01 perfil [5]=mask stages`, lows en `[8..15]`,
highs en `[16..23]`, `[24]=stage_activo+1`, colores **BGR** en `[25..48]`, `[49]=0x02`,
checksum = suma de `payload[3:50]` big-endian en `[50..51]`.

DPI en el alambre: `dpi/50 - 1` en 16 bits LE (el X3 usaba indice; no confundir).

El boton DPI fisico reporta el stage **1-indexado** (`stage_from_event()` lo pasa a 0..7).

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

44+ tests sin hardware: layout del payload 0x04, checksum, round-trip DPI, parseo de
`0x03`, validacion/persistencia de perfiles (incluye migracion del formato viejo) y
maquina de estados de capas (hold, toggle, release diferido, reset).

## Pendiente

- Capturar con usbmon/Wireshark: `0x07` (remap onboard), `0x09` (macros onboard),
  `0x06` (checksum del polling: el corto usa complemento a uno y no coincide con el
  aditivo del 0x04), `0x05` (sleep).
- LOD/debounce/angle-snap/motion-sync: hoy solo se guardan, no se envian.
- Auto-switch por app (`auto_apps`), autostart en Windows, backend `evdev` para Linux.
- Iconos por estado y modo single-instance.
