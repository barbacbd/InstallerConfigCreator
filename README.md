# OpenShift Install Config Creator

A Streamlit web application for interactively generating `install-config.yaml` files for OpenShift clusters. Select a platform, fill in the fields, and download a ready-to-use config.

## Supported Platforms

| Platform | Status |
|----------|--------|
| AWS | Supported |
| Azure | Supported |
| GCP | Supported |
| vSphere | Supported |
| Bare Metal | Supported |
| OpenStack | Supported |
| Nutanix | Supported |
| IBM Cloud | Supported |
| PowerVS | Supported |
| None | Supported |

## Features

- Dynamic form rendering based on selected platform
- Platform-specific fields with correct types, enums, and defaults
- Machine pool configuration (control plane and compute)
- Networking, proxy, and cluster option configuration
- Pull secret and SSH key input via paste, file path, or file upload
- Live YAML preview as you edit
- Download with custom filename

## Prerequisites

- Python 3.9+

## Installation

```bash
git clone https://github.com/<your-org>/install_config_creator.git
cd install_config_creator
pip install -r requirements.txt
```

## Usage

### Basic

```bash
streamlit run app.py
```

This opens the app in your default browser at `http://localhost:8501`.

### Custom Port

```bash
streamlit run app.py --server.port 9090
```

### Headless (no browser auto-open)

```bash
streamlit run app.py --server.headless true
```

### Run in the Background

```bash
streamlit run app.py --server.headless true --server.port 8501 &
```

Stop it later with:

```bash
pkill -f "streamlit run"
```

### Docker

```bash
docker build -t install-config-creator .
docker run -p 8501:8501 install-config-creator
```

Then open `http://localhost:8501` in your browser.

### Quick Start

1. Select a platform from the sidebar
2. Fill in the required fields (marked with `*`)
3. Configure networking, machine pools, and options as needed
4. Review the generated YAML in the right panel
5. Click **Download** to save the file

## Project Structure

```
install_config_creator/
  app.py              # Streamlit application
  requirements.txt    # Python dependencies
  README.md
```

## Configuration Reference

The form fields are derived from the [OpenShift Installer](https://github.com/openshift/installer) Go types in `pkg/types/`. The full schema is also available as a CRD at [`install.openshift.io_installconfigs.yaml`](https://github.com/openshift/installer/blob/main/data/data/install.openshift.io_installconfigs.yaml).

## License

Apache License 2.0
