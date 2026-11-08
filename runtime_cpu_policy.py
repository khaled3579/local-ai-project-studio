"""Optional process-local CPU isolation, discovered from Windows CPU topology."""
import ctypes
import os
import struct


def efficiency_cpus():
    if os.name != 'nt':
        raise RuntimeError('CPU isolation policy requires Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    needed = ctypes.c_ulong()
    kernel.GetSystemCpuSetInformation(None, 0, ctypes.byref(needed), None, 0)
    buffer = ctypes.create_string_buffer(needed.value)
    if not kernel.GetSystemCpuSetInformation(buffer, needed.value, ctypes.byref(needed), None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    rows = []
    offset = 0
    while offset < needed.value:
        size, kind = struct.unpack_from('II', buffer.raw, offset)
        if size < 8 or offset + size > needed.value:
            raise RuntimeError('Invalid Windows CPU topology response')
        if kind == 0:
            rows.append({'group': struct.unpack_from('H', buffer.raw, offset + 12)[0],
                         'logical': buffer.raw[offset + 14], 'efficiency': buffer.raw[offset + 18]})
        offset += size
    return select_efficiency_cpus(rows)


def select_efficiency_cpus(rows):
    classes = {row['efficiency'] for row in rows}
    if len(classes) < 2 or any(row['group'] != 0 for row in rows):
        raise RuntimeError('Efficiency isolation requires a verified single-group hybrid CPU')
    lowest = min(classes)
    return sorted({row['logical'] for row in rows if row['efficiency'] == lowest})


def apply_efficiency_policy():
    selected = efficiency_cpus()
    mask = sum(1 << logical for logical in selected)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    handle = kernel.GetCurrentProcess()
    current, system = ctypes.c_size_t(), ctypes.c_size_t()
    if not kernel.GetProcessAffinityMask(handle, ctypes.byref(current), ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not mask or mask & current.value != mask:
        raise RuntimeError('Selected efficiency CPUs are outside this process affinity')
    if not kernel.SetProcessAffinityMask(handle, mask):
        raise ctypes.WinError(ctypes.get_last_error())
    return selected
