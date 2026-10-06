"""Read-only Windows adapter parameter tests; never start services or scheduled tasks."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).parent
pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows adapter parameter tests')


def run(script, scenario):
    path = str(SCRIPTS / script).replace("'", "''")
    source = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile('__PATH__', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Adapter syntax errors' }
$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @('Resolve-WorkbenchLanHost','Resolve-WorkbenchPython')}, $true) |
    ForEach-Object { Invoke-Expression $_.Extent.Text }
__SCENARIO__
""".replace('__PATH__', path).replace('__SCENARIO__', scenario)
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', source],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


@pytest.mark.parametrize('script', ['Start-Workbench.ps1', 'Install-Autostart.ps1'])
def test_syntax_and_no_private_parameter_defaults(script):
    value = run(script, r"""
$parameters = @{}
foreach ($parameter in $ast.ParamBlock.Parameters) {
    if ($parameter.Name.VariablePath.UserPath -in @('LanHost','PythonPath')) {
        $parameters[$parameter.Name.VariablePath.UserPath] = $null -eq $parameter.DefaultValue
    }
}
$parameters | ConvertTo-Json -Compress
""")
    assert value == {'LanHost': True, 'PythonPath': True}


@pytest.mark.parametrize('script', ['Start-Workbench.ps1', 'Install-Autostart.ps1'])
def test_explicit_lan_host_does_not_read_local_configuration(script):
    value = run(script, r"""
function Get-Content { throw 'Configuration must not be read for an explicit host' }
@{ Explicit = (Resolve-WorkbenchLanHost -Value '192.0.2.10' -ConfigPath 'X:\missing.json') -eq '192.0.2.10' } | ConvertTo-Json -Compress
""")
    assert value == {'Explicit': True}


@pytest.mark.parametrize('script', ['Start-Workbench.ps1', 'Install-Autostart.ps1'])
def test_first_setup_requires_host_and_existing_config_is_read_only(script, tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'LanHost': '192.0.2.10', 'PythonPath': 'X:\\previous\\python.exe'}))
    before = config.read_bytes()
    path = str(config).replace("'", "''")
    missing = str(tmp_path / 'missing.json').replace("'", "''")
    value = run(script, r"""
$blocked = $false
try { Resolve-WorkbenchLanHost -ConfigPath '__MISSING__' | Out-Null } catch { $blocked = $_.Exception.Message -match 'Provide -LanHost' }
@{ Missing = $blocked; Existing = (Resolve-WorkbenchLanHost -ConfigPath '__CONFIG__') -eq '192.0.2.10' } | ConvertTo-Json -Compress
""".replace('__CONFIG__', path).replace('__MISSING__', missing))
    assert value == {'Missing': True, 'Existing': True}
    assert config.read_bytes() == before


@pytest.mark.parametrize('script', ['Start-Workbench.ps1', 'Install-Autostart.ps1'])
def test_invalid_config_error_does_not_echo_its_contents(script, tmp_path):
    config = tmp_path / 'invalid.json'
    config.write_text('not-json-private-sentinel')
    path = str(config).replace("'", "''")
    value = run(script, r"""
try { Resolve-WorkbenchLanHost -ConfigPath '__CONFIG__' | Out-Null; throw 'Expected error' }
catch { @{ Redacted = $_.Exception.Message -notmatch 'private-sentinel'; Guidance = $_.Exception.Message -match '-LanHost' } | ConvertTo-Json -Compress }
""".replace('__CONFIG__', path))
    assert value == {'Redacted': True, 'Guidance': True}


@pytest.mark.parametrize('script', ['Start-Workbench.ps1', 'Install-Autostart.ps1'])
def test_python_explicit_path_and_discovery_are_checked_without_private_defaults(script):
    value = run(script, r"""
function Test-Path { param($LiteralPath, $PathType) return $LiteralPath -eq 'X:\example\python.exe' -and $PathType -eq 'Leaf' }
function Get-Command { param($Name) if ($Name -ne 'py') { throw 'Unexpected discovery command' }; return [pscustomobject]@{Source='fake-py-launcher'} }
function fake-py-launcher { $script:invocation = $args; $global:LASTEXITCODE = 0; 'X:\example\python.exe' }
$explicit = Resolve-WorkbenchPython -Value 'X:\example\python.exe'
$discovered = Resolve-WorkbenchPython
@{ Explicit = $explicit -eq 'X:\example\python.exe'; Discovered = $discovered -eq $explicit; Version = $script:invocation[0] -eq '-3.12'; Probe = $script:invocation[2] -eq 'import sys; print(sys.executable)' } | ConvertTo-Json -Compress
""")
    assert value == {'Explicit': True, 'Discovered': True, 'Version': True, 'Probe': True}


@pytest.mark.parametrize('script', ['Start-Workbench.ps1', 'Install-Autostart.ps1'])
def test_missing_launcher_invalid_path_and_failed_python_discovery_fail_closed(script):
    value = run(script, r"""
function Get-Command { return $null }
$missing = $false
try { Resolve-WorkbenchPython | Out-Null } catch { $missing = $_.Exception.Message -match '-PythonPath' }
function Test-Path { return $false }
$invalid = $false
try { Resolve-WorkbenchPython -Value 'X:\missing\python.exe' | Out-Null } catch { $invalid = $_.Exception.Message -match '-PythonPath' }
function Get-Command { return [pscustomobject]@{Source='failed-py-launcher'} }
function failed-py-launcher { $global:LASTEXITCODE = 1; 'runtime-not-installed' }
$failed = $false
try { Resolve-WorkbenchPython | Out-Null } catch { $failed = $_.Exception.Message -match '-PythonPath' }
@{ Missing = $missing; Invalid = $invalid; Failed = $failed } | ConvertTo-Json -Compress
""")
    assert value == {'Missing': True, 'Invalid': True, 'Failed': True}