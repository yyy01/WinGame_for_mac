// SPDX-License-Identifier: MIT
// No Steam SDK redistribution and no account/session-file access.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
typedef unsigned char(__cdecl *init_fn)(void);
typedef void(__cdecl *shutdown_fn)(void);
int main(void) {
  HMODULE dll =
      LoadLibraryA(sizeof(void *) == 8 ? "steam_api64.dll" : "steam_api.dll");
  if (!dll)
    return 20;
  init_fn init = (init_fn)GetProcAddress(dll, "SteamAPI_Init");
  shutdown_fn shutdown = (shutdown_fn)GetProcAddress(dll, "SteamAPI_Shutdown");
  if (!init || !shutdown)
    return 21;
  if (!init())
    return 22;
  shutdown();
  FreeLibrary(dll);
  return 0;
}
