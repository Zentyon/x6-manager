/* Shim de HID.DLL que registra lo que el software oficial escribe en el mouse.
 *
 * Se coloca junto a una COPIA de X6.exe: como HID.DLL no es KnownDLL, Windows carga
 * este archivo antes que el de System32. Cada funcion reenvia a la DLL real
 * (C:\\Windows\\System32\\hid.dll) y anota los bytes en hid_capture.log.
 *
 * Solo se usa para aprender el protocolo; no modifica nada del mouse por si mismo.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <intrin.h>

typedef void *PREPARSED;

static HMODULE g_real = NULL;
static FILE *g_log = NULL;
static CRITICAL_SECTION g_cs;
static char g_dir[MAX_PATH];

static FARPROC real_proc(const char *name)
{
    return g_real ? GetProcAddress(g_real, name) : NULL;
}

static void log_line(const char *fmt, ...)
{
    SYSTEMTIME st;
    va_list ap;

    if (!g_log) {
        return;
    }
    GetLocalTime(&st);
    EnterCriticalSection(&g_cs);
    fprintf(g_log, "%02d:%02d:%02d.%03d ", st.wHour, st.wMinute, st.wSecond,
            st.wMilliseconds);
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    fflush(g_log);
    LeaveCriticalSection(&g_cs);
}

static void caller_name(char *out, size_t size)
{
    void *addr = _ReturnAddress();
    HMODULE mod = NULL;
    char path[MAX_PATH];

    out[0] = 0;
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                               GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCSTR)addr, &mod) && mod) {
        if (GetModuleFileNameA(mod, path, MAX_PATH)) {
            const char *base = strrchr(path, '\\');
            strncpy(out, base ? base + 1 : path, size - 1);
            out[size - 1] = 0;
        }
    }
}

static void dump(const char *tag, const void *buf, ULONG len, const char *who)
{
    const unsigned char *p = (const unsigned char *)buf;
    char hex[3 * 96 + 4];
    ULONG n = len > 96 ? 96 : len;
    ULONG i;
    int off = 0;

    if (!buf) {
        log_line("%s len=%lu buf=NULL [%s]", tag, len, who);
        return;
    }
    for (i = 0; i < n; i++) {
        off += sprintf(hex + off, "%02x ", p[i]);
    }
    hex[off] = 0;
    log_line("%s len=%lu [%s] %s%s", tag, len, who, hex, len > n ? "..." : "");
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        char path[MAX_PATH];
        char *base;
        InitializeCriticalSection(&g_cs);

        GetModuleFileNameA(inst, path, MAX_PATH);
        base = strrchr(path, '\\');
        if (base) {
            *(base + 1) = 0;
        }
        strncpy(g_dir, path, MAX_PATH - 1);

        g_real = LoadLibraryA("C:\\Windows\\System32\\hid.dll");
        strncat(path, "hid_capture.log", MAX_PATH - strlen(path) - 1);
        g_log = fopen(path, "a");
        log_line("=== proceso %lu cargado, hid.dll real=%p ===",
                 GetCurrentProcessId(), (void *)g_real);
    } else if (reason == DLL_PROCESS_DETACH) {
        if (g_log) {
            log_line("=== fin del proceso ===");
            fclose(g_log);
            g_log = NULL;
        }
        DeleteCriticalSection(&g_cs);
    }
    return TRUE;
}

__declspec(dllexport) BOOLEAN WINAPI HidD_SetFeature(HANDLE dev, PVOID buf, ULONG len)
{
    typedef BOOLEAN(WINAPI * FN)(HANDLE, PVOID, ULONG);
    char who[64];
    caller_name(who, sizeof who);
    dump("SET_FEATURE", buf, len, who);
    return ((FN)real_proc("HidD_SetFeature"))(dev, buf, len);
}

__declspec(dllexport) BOOLEAN WINAPI HidD_GetFeature(HANDLE dev, PVOID buf, ULONG len)
{
    typedef BOOLEAN(WINAPI * FN)(HANDLE, PVOID, ULONG);
    char who[64];
    BOOLEAN ok;
    caller_name(who, sizeof who);
    log_line("GET_FEATURE request len=%lu [%s]", len, who);
    ok = ((FN)real_proc("HidD_GetFeature"))(dev, buf, len);
    dump(ok ? "GET_FEATURE ok" : "GET_FEATURE fail", buf, len, who);
    return ok;
}

__declspec(dllexport) BOOLEAN WINAPI HidD_GetAttributes(HANDLE dev, PVOID attrs)
{
    typedef BOOLEAN(WINAPI * FN)(HANDLE, PVOID);
    return ((FN)real_proc("HidD_GetAttributes"))(dev, attrs);
}

__declspec(dllexport) VOID WINAPI HidD_GetHidGuid(LPGUID guid)
{
    typedef VOID(WINAPI * FN)(LPGUID);
    ((FN)real_proc("HidD_GetHidGuid"))(guid);
}

__declspec(dllexport) BOOLEAN WINAPI HidD_GetPreparsedData(HANDLE dev, PREPARSED *p)
{
    typedef BOOLEAN(WINAPI * FN)(HANDLE, PREPARSED *);
    return ((FN)real_proc("HidD_GetPreparsedData"))(dev, p);
}

__declspec(dllexport) BOOLEAN WINAPI HidD_FreePreparsedData(PREPARSED p)
{
    typedef BOOLEAN(WINAPI * FN)(PREPARSED);
    return ((FN)real_proc("HidD_FreePreparsedData"))(p);
}

__declspec(dllexport) LONG WINAPI HidP_GetCaps(PREPARSED p, PVOID caps)
{
    typedef LONG(WINAPI * FN)(PREPARSED, PVOID);
    return ((FN)real_proc("HidP_GetCaps"))(p, caps);
}

/* Passthroughs por si el software las resuelve dinamicamente. */
__declspec(dllexport) BOOLEAN WINAPI HidD_SetOutputReport(HANDLE d, PVOID b, ULONG l)
{
    typedef BOOLEAN(WINAPI * FN)(HANDLE, PVOID, ULONG);
    char who[64];
    caller_name(who, sizeof who);
    dump("SET_OUTPUT", b, l, who);
    return ((FN)real_proc("HidD_SetOutputReport"))(d, b, l);
}

__declspec(dllexport) BOOLEAN WINAPI HidD_GetInputReport(HANDLE d, PVOID b, ULONG l)
{
    typedef BOOLEAN(WINAPI * FN)(HANDLE, PVOID, ULONG);
    return ((FN)real_proc("HidD_GetInputReport"))(d, b, l);
}

__declspec(dllexport) BOOLEAN WINAPI HidD_SetNumInputBuffers(HANDLE d, ULONG n)
{
    typedef BOOLEAN(WINAPI * FN)(HANDLE, ULONG);
    return ((FN)real_proc("HidD_SetNumInputBuffers"))(d, n);
}
