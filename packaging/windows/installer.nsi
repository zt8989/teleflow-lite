; TeleFlow NSIS Installer Script
; Produces a modern Windows installer for TeleFlow.
;
; Supports two installation scopes:
;   - All users (installs to Program Files, HKLM registry) — requires admin
;   - Current user only (installs to %LOCALAPPDATA%, HKCU registry) — no admin
;
; The installer starts without UAC. If the user selects "all users", it
; re-launches itself with the /ALL flag via `ShellExec runas` to trigger
; elevation only when actually needed.
;
; Usage:
;   makensis installer.nsi
; Or via build.ps1 which calls makensis automatically after PyInstaller.

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "WinMessages.nsh"

; ─── Configuration ────────────────────────────────────────────────────────────

!define APP_NAME        "TeleFlow"
!define APP_VERSION     "0.1.0"
!define APP_PUBLISHER   "TeleFlow"
!define APP_WEB_SITE    "https://github.com/teleflow/teleflow-lite"
!define APP_EXE         "TeleFlow.exe"
!define APP_UNINST_KEY  "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define APP_REG_KEY     "Software\${APP_NAME}"

!define BUILD_DIR       "dist\TeleFlow"
!define ICON            "TeleFlow.ico"

; ─── Variables ────────────────────────────────────────────────────────────────

Var InstScope          ; "all" or "user"
Var IsElevated         ; "yes" if running with admin (after re-launch)

; ─── General ──────────────────────────────────────────────────────────────────

Name "${APP_NAME} ${APP_VERSION}"
OutFile "..\..\${APP_NAME}-windows-${APP_VERSION}-setup.exe"
RequestExecutionLevel user
SetCompressor /SOLID lzma

; ─── Version info embedded in the .exe ────────────────────────────────────────

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "CompanyName"     "${APP_PUBLISHER}"
VIAddVersionKey "FileDescription" "${APP_NAME} Installer"
VIAddVersionKey "LegalCopyright"  "Copyright (c) ${APP_PUBLISHER}"

; ─── MUI Settings ─────────────────────────────────────────────────────────────

!define MUI_ABORTWARNING
!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "${ICON}"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${ICON}"

; ─── Pages ────────────────────────────────────────────────────────────────────

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
Page custom fnc_InstScope_Show fnc_InstScope_Leave
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; ─── Languages ────────────────────────────────────────────────────────────────

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; ─── Installer initialization ────────────────────────────────────────────────

Function .onInit
  StrCpy $InstScope "user"
  StrCpy $IsElevated "no"

  ; Check if re-launched with /ALL flag (admin instance)
  ; GetOptions requires a value, so we use a workaround: check $CMDLINE directly
  ${GetParameters} $0
  ; Try to find "/ALL" by checking common parameter patterns
  ClearErrors
  ${GetOptions} $0 "/ALL=" $1
  ${If} ${Errors}
    ; /ALL without value — try searching $0 for "/ALL"
    StrCpy $1 $0 4 0
    ${If} $1 == "/ALL"
      StrCpy $InstScope "all"
      StrCpy $IsElevated "yes"
      StrCpy $INSTDIR "$PROGRAMFILES64\${APP_NAME}"
      SetShellVarContext all
    ${Else}
      StrCpy $INSTDIR "$LOCALAPPDATA\${APP_NAME}"
      SetShellVarContext current
    ${EndIf}
  ${Else}
    StrCpy $InstScope "all"
    StrCpy $IsElevated "yes"
    StrCpy $INSTDIR "$PROGRAMFILES64\${APP_NAME}"
    SetShellVarContext all
  ${EndIf}
FunctionEnd

; ─── Scope selection page ────────────────────────────────────────────────────

Function fnc_InstScope_Show
  ; Skip if already elevated (re-launched instance)
  ${If} $IsElevated == "yes"
    Abort
  ${EndIf}

  !insertMacro MUI_HEADER_TEXT "Select Installation Scope" "Choose whether to install ${APP_NAME} for all users or only the current user"

  nsDialogs::Create 1018
  Pop $0

  ${If} $0 == error
    Abort
  ${EndIf}

  ; Radio button: Current user (default, no admin needed)
  ${NSD_CreateRadioButton} 30u 40u 220u 12u "Install for current user only (no admin required)"
  Pop $0
  SendMessage $0 ${BM_SETCHECK} ${BST_CHECKED} 0

  ; Radio button: All users (requires admin)
  ${NSD_CreateRadioButton} 30u 60u 220u 12u "Install for all users (requires admin)"
  Pop $1

  ; Description text
  ${NSD_CreateLabel} 30u 85u 240u 30u \
    "Current user: Install to %LOCALAPPDATA%$\nAll users: Install to Program Files (admin required)"
  Pop $2

  nsDialogs::Show
FunctionEnd

Function fnc_InstScope_Leave
  ; Skip if already elevated
  ${If} $IsElevated == "yes"
    Return
  ${EndIf}

  ; Check which radio button is checked — $0 is the first radio (current user)
  SendMessage $0 ${BM_GETCHECK} 0 0 $2
  ${If} $2 == ${BST_CHECKED}
    StrCpy $InstScope "user"
    StrCpy $INSTDIR "$LOCALAPPDATA\${APP_NAME}"
    SetShellVarContext current
  ${Else}
    ; User chose "all users" — need to re-launch with admin rights
    StrCpy $InstScope "all"

    ; Get path to this installer
    Call .GetExePath
    Pop $3

    ; Re-launch with /ALL=yes flag using runas verb (triggers UAC)
    ExecShell "runas" "$3" "/ALL=yes"

    ; Quit this non-admin instance (the elevated one will take over)
    Quit
  ${EndIf}
FunctionEnd

; ─── Helper: Get path to current executable ──────────────────────────────────

Function .GetExePath
  System::Call 'kernel32::GetModuleFileName(t, t .r0, i 1024) i'
  Push $0
FunctionEnd

; ─── Installer Sections ──────────────────────────────────────────────────────

Section "Install" SEC_INSTALL
  SetOutPath "$INSTDIR"
  Setoverwrite on

  ; Bundle the entire PyInstaller onedir output
  File /r "${BUILD_DIR}\*.*"

  ; Application icon
  File "${ICON}"

  ; Store installation path and scope in registry
  ${If} $InstScope == "all"
    WriteRegStr HKLM "${APP_REG_KEY}" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "${APP_REG_KEY}" "Scope" "all"
  ${Else}
    WriteRegStr HKCU "${APP_REG_KEY}" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "${APP_REG_KEY}" "Scope" "user"
  ${EndIf}

  ; Create uninstaller
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Start Menu shortcuts
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut  "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortCut  "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk"   "$INSTDIR\uninstall.exe"

  ; Desktop shortcut
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"

  ; Add/Remove Programs entry
  ${If} $InstScope == "all"
    WriteRegStr   HKLM "${APP_UNINST_KEY}" "DisplayName"     "${APP_NAME}"
    WriteRegStr   HKLM "${APP_UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
    WriteRegStr   HKLM "${APP_UNINST_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr   HKLM "${APP_UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
    WriteRegStr   HKLM "${APP_UNINST_KEY}" "Publisher"       "${APP_PUBLISHER}"
    WriteRegStr   HKLM "${APP_UNINST_KEY}" "URLInfoAbout"    "${APP_WEB_SITE}"
    WriteRegDWORD HKLM "${APP_UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKLM "${APP_UNINST_KEY}" "NoRepair" 1
  ${Else}
    WriteRegStr   HKCU "${APP_UNINST_KEY}" "DisplayName"     "${APP_NAME}"
    WriteRegStr   HKCU "${APP_UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
    WriteRegStr   HKCU "${APP_UNINST_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr   HKCU "${APP_UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
    WriteRegStr   HKCU "${APP_UNINST_KEY}" "Publisher"       "${APP_PUBLISHER}"
    WriteRegStr   HKCU "${APP_UNINST_KEY}" "URLInfoAbout"    "${APP_WEB_SITE}"
    WriteRegDWORD HKCU "${APP_UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${APP_UNINST_KEY}" "NoRepair" 1
  ${EndIf}

  ; Calculate and store installed size
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  ${If} $InstScope == "all"
    WriteRegDWORD HKLM "${APP_UNINST_KEY}" "EstimatedSize" "$0"
  ${Else}
    WriteRegDWORD HKCU "${APP_UNINST_KEY}" "EstimatedSize" "$0"
  ${EndIf}
SectionEnd

; ─── Uninstaller Section ─────────────────────────────────────────────────────

Section "Uninstall"
  ; Remove files (everything in $INSTDIR)
  RMDir /r "$INSTDIR"

  ; Remove Start Menu shortcuts
  RMDir /r "$SMPROGRAMS\${APP_NAME}"

  ; Remove desktop shortcut
  Delete "$DESKTOP\${APP_NAME}.lnk"

  ; Detect scope from registry to know which root to clean
  ClearErrors
  ReadRegStr $0 HKLM "${APP_REG_KEY}" "Scope"
  ${IfNot} ${Errors}
    DeleteRegKey HKLM "${APP_UNINST_KEY}"
    DeleteRegKey HKLM "${APP_REG_KEY}"
  ${Else}
    ClearErrors
    ReadRegStr $0 HKCU "${APP_REG_KEY}" "Scope"
    ${IfNot} ${Errors}
      DeleteRegKey HKCU "${APP_UNINST_KEY}"
      DeleteRegKey HKCU "${APP_REG_KEY}"
    ${EndIf}
  ${EndIf}
SectionEnd
