"""Install the OCR engine and verified test models on disposable GitHub runners."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


MODELS = {
    'eng': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',
    'rus': 'e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225',
}
MODEL_URL = 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/'
WINDOWS_RELEASE = 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/'


def verified_download(url, output, expected):
    curl = 'curl.exe' if os.name == 'nt' else 'curl'
    subprocess.run([curl, '--fail', '--silent', '--show-error', '--location', '--retry', '3',
                    '--max-time', '180', '--proto', '=https', '--proto-redir', '=https',
                    '--output', str(output), url], check=True)
    if sha256(output.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f'Unexpected SHA-256 for {output.name}')


def main():
    runner = os.environ.get('RUNNER_OS')
    if os.environ.get('GITHUB_ACTIONS') != 'true' or runner not in ('Windows', 'macOS'):
        raise SystemExit('This installer is for disposable GitHub Windows and macOS runners only.')
    temporary = Path(os.environ['RUNNER_TEMP'])
    if runner == 'Windows':
        with TemporaryDirectory(prefix='ocr-installer-', dir=temporary) as folder:
            installer = Path(folder) / 'tesseract-ocr-w64-setup-5.4.0.20240606.exe'
            verified_download(WINDOWS_RELEASE + installer.name, installer,
                              'c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9')
            subprocess.run([str(installer), '/S'], check=True, timeout=300)
        executable = Path(os.environ['ProgramFiles']) / 'Tesseract-OCR/tesseract.exe'
    else:
        installed = subprocess.run(['brew', 'list', '--versions', 'tesseract'], capture_output=True, text=True)
        if installed.returncode != 0 or not installed.stdout.strip():
            subprocess.run(['brew', 'install', 'tesseract'], check=True, timeout=720)
        prefix = subprocess.check_output(['brew', '--prefix', 'tesseract'], text=True).strip()
        executable = Path(prefix) / 'bin/tesseract'
    models = temporary / 'exhibit-ci-tessdata'
    models.mkdir(exist_ok=True)
    for code, expected in MODELS.items():
        name = code + '.traineddata'
        verified_download(MODEL_URL + name, models / name, expected)
    subprocess.run([str(executable), '--version'], check=True)
    with Path(os.environ['GITHUB_ENV']).open('a', encoding='utf-8') as env:
        env.write(f'EXHIBIT_TESSERACT={executable}\nEXHIBIT_TESSDATA={models}\n')


if __name__ == '__main__':
    main()
