import os
import sys

import pefile

path = os.path.join(os.environ["TEMP"], "x6-capture", "X6.exe")
pe = pefile.PE(path, fast_load=True)
pe.parse_data_directories(
    directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
)
found = "?"
for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
    if entry.id != 24:
        continue
    for e2 in entry.directory.entries:
        for e3 in e2.directory.entries:
            data = pe.get_data(e3.data.struct.OffsetToData, e3.data.struct.Size)
            text = data.decode("utf-8", "replace")
            if "asInvoker" in text:
                found = "asInvoker"
            elif "requireAdministrator" in text:
                found = "requireAdministrator"
print("nivel de ejecucion en la copia:", found)
sys.exit(0 if found == "asInvoker" else 1)
