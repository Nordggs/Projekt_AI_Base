# Запуск AI-Chats Pipeline
# Использование:
#   .\run-pipeline.ps1              — запуск в режиме ожидания (watchdog)
#   .\run-pipeline.ps1 -Serve       — watchdog + HTTP-сервер (рекомендуется)
#   .\run-pipeline.ps1 -Once        — обработать inbox и выйти
#   .\run-pipeline.ps1 -Model deepseek-r1:14b  — с другой моделью

param(
    [switch]$Once,
    [switch]$Serve,
    [string]$Model
)

$ScriptArgs = @()
if ($Once) { $ScriptArgs += "--once" }
if ($Serve) { $ScriptArgs += "--serve" }
if ($Model) { $ScriptArgs += "--model"; $ScriptArgs += $Model }

python pipeline.py @ScriptArgs
