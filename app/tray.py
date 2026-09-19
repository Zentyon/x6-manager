"""Bandeja del sistema: cambio rapido de perfil. Win+Linux via pystray."""
import pystray
from PIL import Image, ImageDraw

def make_icon():
    img = Image.new("RGB", (64, 64), (20, 20, 25))
    dr = ImageDraw.Draw(img)
    dr.ellipse([12, 12, 52, 52], fill=(0, 200, 120))
    return img

def run_tray(daemon):
    from pystray import MenuItem, Menu
    def items():
        ms = [MenuItem(f"{'● ' if n == daemon.active else ''}{n}", lambda _, n=n: daemon.switch(n))
              for n in sorted(daemon.profiles)]
        ms += [Menu.SEPARATOR,
               MenuItem("Siguiente perfil", lambda _: daemon.cycle(+1)),
               MenuItem("Recargar", lambda _: daemon.reload()),
               MenuItem("Salir", lambda icon, _: icon.stop())]
        return ms
    icon = pystray.Icon("x6", make_icon(), f"X6 [{daemon.active}]", menu=pystray.Menu(*items()))
    # refrescar titulo al cambiar: el daemon actualiza daemon.active
    icon.run()
