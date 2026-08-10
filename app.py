"""OpenShift install-config.yaml Creator

Interactive Streamlit application for generating OpenShift install-config.yaml files.
Dynamically renders platform-specific fields based on the selected platform.
"""

import ipaddress
import re
from pathlib import Path

import streamlit as st
import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLATFORMS = [
    "aws", "azure", "gcp", "vsphere", "baremetal",
    "openstack", "nutanix", "ibmcloud", "powervs", "none",
]

PLATFORM_LABELS = {
    "aws": "AWS",
    "azure": "Azure",
    "gcp": "GCP",
    "vsphere": "vSphere",
    "baremetal": "Bare Metal",
    "openstack": "OpenStack",
    "nutanix": "Nutanix",
    "ibmcloud": "IBM Cloud",
    "powervs": "PowerVS",
    "none": "None",
}

PUBLISH_STRATEGIES = ["External", "Internal", "Mixed"]
CREDENTIALS_MODES = ["", "Mint", "Passthrough", "Manual"]
CPU_PARTITIONING = ["None", "AllNodes"]
NETWORK_TYPES = ["OVNKubernetes"]


# ---------------------------------------------------------------------------
# YAML / dict helpers
# ---------------------------------------------------------------------------

def read_file_content(uploaded_file, file_path: str) -> str | None:
    """Resolve content from an uploaded file, a local file path, or None."""
    if uploaded_file is not None:
        return uploaded_file.read().decode("utf-8", errors="replace").strip()
    if file_path:
        resolved = Path(file_path).expanduser().resolve()
        if resolved.is_file():
            return resolved.read_text(encoding="utf-8", errors="replace").strip()
    return None


def clean_dict(d):
    """Recursively remove None, empty string, empty list, and empty dict values."""
    if not isinstance(d, dict):
        return d
    cleaned = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = clean_dict(v)
            if v:
                cleaned[k] = v
        elif isinstance(v, list):
            v = [clean_dict(i) if isinstance(i, dict) else i for i in v if i is not None]
            v = [i for i in v if i != {} and i != "" and i != []]
            if v:
                cleaned[k] = v
        elif isinstance(v, bool):
            cleaned[k] = v
        elif v is not None and v != "":
            cleaned[k] = v
    return cleaned


def render_yaml(config):
    """Render a config dict as YAML, stripping empty values."""
    return yaml.dump(clean_dict(config), default_flow_style=False, sort_keys=False)


def _resolve_path(data, path):
    """Walk a nested dict/list by path tuple, returning None on miss."""
    for part in path:
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and isinstance(part, int) and part < len(data):
            data = data[part]
        else:
            return None
    return data


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_PLATFORM_REQUIRED = {
    "aws":       [("region",)],
    "azure":     [("region",)],
    "gcp":       [("projectID",), ("region",)],
    "vsphere":   [("vcenters",), ("failureDomains",)],
    "baremetal": [("hosts",)],
    "openstack": [("cloud",)],
    "nutanix":   [("prismCentral",), ("prismElements",), ("subnetUUIDs",)],
    "ibmcloud":  [("region",)],
    "powervs":   [("powervsResourceGroup",), ("zone",), ("userID",)],
}


def _is_valid_cidr(cidr):
    try:
        ipaddress.ip_network(cidr, strict=False)
        return True
    except (ValueError, TypeError):
        return False


def _validate_networking(networking, errors):
    for field in ("machineNetwork", "clusterNetwork"):
        for net in networking.get(field, []):
            if isinstance(net, dict):
                cidr = net.get("cidr", "")
                if cidr and not _is_valid_cidr(cidr):
                    errors.append(f"Invalid CIDR in networking.{field}: {cidr}")
                if field == "clusterNetwork" and "hostPrefix" in net:
                    hp = net["hostPrefix"]
                    if not isinstance(hp, int) or not 1 <= hp <= 128:
                        errors.append("networking.clusterNetwork.hostPrefix must be 1-128")
    for svc in networking.get("serviceNetwork", []):
        if isinstance(svc, str) and svc and not _is_valid_cidr(svc):
            errors.append(f"Invalid CIDR in networking.serviceNetwork: {svc}")


def _validate_platform_fields(platform, errors):
    for plat_key, fields in _PLATFORM_REQUIRED.items():
        if plat_key in platform and isinstance(platform[plat_key], dict):
            for (field,) in fields:
                if not platform[plat_key].get(field):
                    errors.append(f"platform.{plat_key}.{field} is required")


def validate_yaml(yaml_str):
    """Validate an install-config YAML string."""
    errors = []
    warnings = []

    if not yaml_str or not yaml_str.strip():
        return {"valid": False, "errors": ["YAML content is empty"], "warnings": []}

    try:
        config = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        return {"valid": False, "errors": [f"YAML syntax error: {e}"], "warnings": []}

    if not isinstance(config, dict):
        return {"valid": False, "errors": ["YAML root must be a mapping"], "warnings": []}

    api_version = config.get("apiVersion")
    if not api_version:
        errors.append("Missing required field: apiVersion")
    elif api_version != "v1":
        warnings.append(f"Expected apiVersion 'v1', got '{api_version}'")

    if not config.get("baseDomain"):
        errors.append("Missing required field: baseDomain")

    metadata = config.get("metadata")
    name = metadata.get("name", "") if isinstance(metadata, dict) else ""
    if not name:
        errors.append("Missing required field: metadata.name")
    elif not re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$', str(name)):
        warnings.append("metadata.name should be a valid DNS label "
                         "(lowercase alphanumeric and hyphens)")

    if not config.get("pullSecret"):
        errors.append("Missing required field: pullSecret")

    platform = config.get("platform")
    if not platform:
        errors.append("Missing required field: platform")
    elif isinstance(platform, dict):
        known = set(PLATFORMS)
        for pk in platform:
            if pk not in known:
                warnings.append(f"Unknown platform: {pk}")
        _validate_platform_fields(platform, errors)

    networking = config.get("networking")
    if isinstance(networking, dict):
        _validate_networking(networking, errors)

    cp = config.get("controlPlane")
    if isinstance(cp, dict) and "replicas" in cp:
        r = cp["replicas"]
        if not isinstance(r, int) or r < 1:
            errors.append("controlPlane.replicas must be a positive integer")

    for i, pool in enumerate(config.get("compute", [])):
        if isinstance(pool, dict) and "replicas" in pool:
            r = pool["replicas"]
            if not isinstance(r, int) or r < 0:
                errors.append(f"compute[{i}].replicas must be a non-negative integer")

    publish = config.get("publish")
    if publish and publish not in PUBLISH_STRATEGIES:
        warnings.append(f"Unrecognized publish strategy: {publish}")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# YAML <-> Form sync
# ---------------------------------------------------------------------------

# (yaml_path, widget_key, widget_type)
_YAML_FORM_MAP = [
    (("metadata", "name"),                      "cluster_name",  "text"),
    (("baseDomain",),                           "base_domain",   "text"),
    (("pullSecret",),                           "ps_text",       "text"),
    (("sshKey",),                               "ssh_text",      "text"),
    (("networking", "networkType"),              "network_type",  "select"),
    (("networking", "machineNetwork", 0, "cidr"), "machine_cidr", "text"),
    (("networking", "serviceNetwork", 0),        "service_cidr",  "text"),
    (("networking", "clusterNetwork", 0, "cidr"), "cluster_cidr", "text"),
    (("networking", "clusterNetwork", 0, "hostPrefix"), "host_prefix", "number"),
    (("publish",),                              "publish",       "select"),
    (("fips",),                                 "fips",          "checkbox"),
    (("credentialsMode",),                      "creds_mode",    "select"),
    (("cpuPartitioningMode",),                  "cpu_part",      "select"),
    (("controlPlane", "replicas"),              "cp_replicas",   "number"),
    (("compute", 0, "replicas"),                "worker_replicas", "number"),
    (("proxy", "httpProxy"),                    "http_proxy",    "text"),
    (("proxy", "httpsProxy"),                   "https_proxy",   "text"),
    (("proxy", "noProxy"),                      "no_proxy",      "text"),
]

_PLATFORM_FORM_MAP = {
    "aws": [
        (("region",),          "aws_region",  "text"),
        (("hostedZone",),      "aws_hz",      "text"),
        (("hostedZoneRole",),  "aws_hz_role", "text"),
    ],
    "azure": [
        (("region",),                        "az_region",  "text"),
        (("resourceGroupName",),             "az_rg",      "text"),
        (("baseDomainResourceGroupName",),   "az_base_rg", "text"),
        (("networkResourceGroupName",),      "az_net_rg",  "text"),
        (("virtualNetwork",),               "az_vnet",    "text"),
    ],
    "gcp": [
        (("projectID",),          "gcp_project",    "text"),
        (("region",),             "gcp_region",     "text"),
        (("network",),            "gcp_network",    "text"),
        (("networkProjectID",),   "gcp_net_proj",   "text"),
        (("controlPlaneSubnet",), "gcp_cp_subnet",  "text"),
        (("computeSubnet",),      "gcp_comp_subnet", "text"),
    ],
    "openstack": [
        (("cloud",),              "os_cloud",    "text"),
        (("externalNetwork",),    "os_ext_net",  "text"),
        (("apiFloatingIP",),      "os_api_fip",  "text"),
        (("ingressFloatingIP",),  "os_ing_fip",  "text"),
        (("clusterOSImage",),     "os_image",    "text"),
    ],
    "ibmcloud": [
        (("region",),             "ibm_region", "text"),
        (("resourceGroupName",),  "ibm_rg",     "text"),
        (("vpcName",),            "ibm_vpc",    "text"),
    ],
    "powervs": [
        (("powervsResourceGroup",), "pvs_rg",     "text"),
        (("zone",),                 "pvs_zone",   "text"),
        (("region",),               "pvs_region", "text"),
        (("userID",),               "pvs_uid",    "text"),
    ],
}

_SELECTBOX_OPTIONS = {
    "network_type":    NETWORK_TYPES,
    "publish":         PUBLISH_STRATEGIES,
    "creds_mode":      CREDENTIALS_MODES,
    "cpu_part":        CPU_PARTITIONING,
    "platform_select": PLATFORMS,
}


def _set_widget(widget_key, value, wtype):
    """Set a single widget's session-state value, silently skipping on error."""
    try:
        if wtype == "text":
            st.session_state[widget_key] = str(value)
        elif wtype == "number" and isinstance(value, (int, float)):
            st.session_state[widget_key] = int(value)
        elif wtype == "select":
            str_val = str(value)
            if str_val in _SELECTBOX_OPTIONS.get(widget_key, []):
                st.session_state[widget_key] = str_val
        elif wtype == "checkbox":
            st.session_state[widget_key] = bool(value)
    except Exception:
        pass


def sync_yaml_to_form(yaml_str):
    """Parse edited YAML and push values back to corresponding form widgets."""
    try:
        config = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        return
    if not isinstance(config, dict):
        return

    for path, widget_key, wtype in _YAML_FORM_MAP:
        value = _resolve_path(config, path)
        if value is not None:
            _set_widget(widget_key, value, wtype)

    platform = config.get("platform", {})
    if not isinstance(platform, dict):
        return
    for plat_key, plat_config in platform.items():
        if plat_key in PLATFORMS:
            _set_widget("platform_select", plat_key, "select")
        if isinstance(plat_config, dict) and plat_key in _PLATFORM_FORM_MAP:
            for path, widget_key, wtype in _PLATFORM_FORM_MAP[plat_key]:
                value = _resolve_path(plat_config, path)
                if value is not None:
                    _set_widget(widget_key, value, wtype)


# ---------------------------------------------------------------------------
# Streamlit widgets — file input helper
# ---------------------------------------------------------------------------

def file_or_text_input(label, text_key, path_key, upload_key, height=100, help_text=""):
    """Render a combined widget: file uploader + file path + manual text area."""
    tabs = st.tabs(["Paste", "File Path", "Upload"])
    with tabs[0]:
        manual = st.text_area(label, height=height, help=help_text, key=text_key)
    with tabs[1]:
        file_path = st.text_input(f"{label} file path", key=path_key,
                                  help="Absolute or ~ path, e.g. ~/.ssh/id_rsa.pub")
    with tabs[2]:
        uploaded = st.file_uploader(f"Upload {label}", key=upload_key)

    from_file = read_file_content(uploaded, file_path)
    return from_file if from_file else manual


# ---------------------------------------------------------------------------
# Common fields
# ---------------------------------------------------------------------------

def render_common_fields():
    """Render the common install-config fields and return the partial config dict."""
    st.header("Cluster Details")
    col1, col2 = st.columns(2)
    with col1:
        cluster_name = st.text_input("Cluster Name *", key="cluster_name",
                                      help="metadata.name — must be a valid DNS label")
    with col2:
        base_domain = st.text_input("Base Domain *", key="base_domain",
                                     help="e.g. example.com")

    pull_secret = file_or_text_input(
        "Pull Secret *", text_key="ps_text", path_key="ps_path", upload_key="ps_upload",
        height=100, help_text="JSON pull secret from cloud.redhat.com",
    )
    ssh_key = file_or_text_input(
        "SSH Public Key", text_key="ssh_text", path_key="ssh_path", upload_key="ssh_upload",
        height=68, help_text="Contents of ~/.ssh/id_rsa.pub",
    )

    st.header("Networking")
    col1, col2 = st.columns(2)
    with col1:
        network_type = st.selectbox("Network Type", NETWORK_TYPES, key="network_type")
        machine_cidr = st.text_input("Machine Network CIDR", value="10.0.0.0/16",
                                      key="machine_cidr")
        service_cidr = st.text_input("Service Network CIDR", value="172.30.0.0/16",
                                      key="service_cidr")
    with col2:
        cluster_cidr = st.text_input("Cluster Network CIDR", value="10.128.0.0/14",
                                      key="cluster_cidr")
        host_prefix = st.number_input("Host Prefix", value=23, min_value=1, max_value=128,
                                       key="host_prefix")

    st.header("Options")
    col1, col2, col3 = st.columns(3)
    with col1:
        publish = st.selectbox("Publish Strategy", PUBLISH_STRATEGIES, key="publish")
        fips = st.checkbox("FIPS Mode", key="fips")
    with col2:
        creds_mode = st.selectbox("Credentials Mode", CREDENTIALS_MODES, key="creds_mode")
        cpu_part = st.selectbox("CPU Partitioning", CPU_PARTITIONING, key="cpu_part")
    with col3:
        cp_replicas = st.number_input("Control Plane Replicas", value=3, min_value=1,
                                       max_value=9, key="cp_replicas")
        worker_replicas = st.number_input("Worker Replicas", value=3, min_value=0,
                                           max_value=100, key="worker_replicas")

    st.header("Proxy (optional)")
    col1, col2, col3 = st.columns(3)
    with col1:
        http_proxy = st.text_input("HTTP Proxy", key="http_proxy")
    with col2:
        https_proxy = st.text_input("HTTPS Proxy", key="https_proxy")
    with col3:
        no_proxy = st.text_input("No Proxy", key="no_proxy")

    proxy = {}
    if http_proxy:
        proxy["httpProxy"] = http_proxy
    if https_proxy:
        proxy["httpsProxy"] = https_proxy
    if no_proxy:
        proxy["noProxy"] = no_proxy

    config = {
        "apiVersion": "v1",
        "metadata": {"name": cluster_name},
        "baseDomain": base_domain,
        "networking": {
            "networkType": network_type,
            "machineNetwork": [{"cidr": machine_cidr}],
            "clusterNetwork": [{"cidr": cluster_cidr, "hostPrefix": host_prefix}],
            "serviceNetwork": [service_cidr],
        },
        "controlPlane": {"name": "master", "replicas": cp_replicas},
        "compute": [{"name": "worker", "replicas": worker_replicas}],
        "publish": publish,
        "pullSecret": pull_secret,
    }
    if ssh_key:
        config["sshKey"] = ssh_key
    if fips:
        config["fips"] = True
    if creds_mode:
        config["credentialsMode"] = creds_mode
    if cpu_part != "None":
        config["cpuPartitioningMode"] = cpu_part
    if proxy:
        config["proxy"] = proxy

    return config


# ---------------------------------------------------------------------------
# Platform forms
# ---------------------------------------------------------------------------

def render_aws():
    st.subheader("AWS Configuration")
    col1, col2 = st.columns(2)
    with col1:
        region = st.text_input("Region *", value="us-east-1", key="aws_region")
        hosted_zone = st.text_input("Hosted Zone", key="aws_hz")
        hosted_zone_role = st.text_input("Hosted Zone Role ARN", key="aws_hz_role")
    with col2:
        lb_type = st.selectbox("Load Balancer Type", ["NLB", "Classic"], key="aws_lb")
        ip_family = st.selectbox("IP Family",
                                  ["IPv4", "DualStackIPv4Primary", "DualStackIPv6Primary"],
                                  key="aws_ipf")

    user_tags_str = st.text_input("User Tags (key=value, comma separated)", key="aws_tags")
    user_tags = {}
    if user_tags_str:
        for pair in user_tags_str.split(","):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                user_tags[k.strip()] = v.strip()

    subnets_str = st.text_input("Subnet IDs (comma separated)", key="aws_subnets")
    subnets = [s.strip() for s in subnets_str.split(",") if s.strip()] if subnets_str else []

    platform = {"region": region}
    if lb_type != "NLB":
        platform["lbType"] = lb_type
    if ip_family != "IPv4":
        platform["ipFamily"] = ip_family
    if hosted_zone:
        platform["hostedZone"] = hosted_zone
    if hosted_zone_role:
        platform["hostedZoneRole"] = hosted_zone_role
    if user_tags:
        platform["userTags"] = user_tags
    if subnets:
        platform["vpc"] = {"subnets": [{"id": s} for s in subnets]}

    return platform, _render_aws_machinepool()


def _render_aws_machinepool():
    st.markdown("**AWS Machine Pool Defaults**")
    col1, col2 = st.columns(2)
    with col1:
        instance_type = st.text_input("Instance Type", key="aws_inst")
        zones_str = st.text_input("Zones (comma separated)", key="aws_zones")
    with col2:
        root_size = st.number_input("Root Volume Size (GB)", value=120, min_value=1,
                                     key="aws_rvs")
        root_type = st.selectbox("Root Volume Type", ["gp3", "gp2", "io1", "io2"],
                                  key="aws_rvt")
    mp = {}
    if instance_type:
        mp["type"] = instance_type
    if zones_str:
        mp["zones"] = [z.strip() for z in zones_str.split(",") if z.strip()]
    if root_size or root_type:
        mp["rootVolume"] = {"size": root_size, "type": root_type}
    return mp


def render_azure():
    st.subheader("Azure Configuration")
    col1, col2 = st.columns(2)
    with col1:
        region = st.text_input("Region *", value="eastus", key="az_region")
        rg_name = st.text_input("Resource Group Name", key="az_rg")
        base_rg = st.text_input("Base Domain Resource Group Name", key="az_base_rg")
    with col2:
        cloud_name = st.selectbox("Cloud Name", [
            "AzurePublicCloud", "AzureUSGovernmentCloud",
            "AzureChinaCloud", "AzureGermanCloud", "AzureStackCloud",
        ], key="az_cloud")
        outbound = st.selectbox("Outbound Type", [
            "Loadbalancer", "NATGatewaySingleZone", "NATGatewayMultiZone",
            "UserDefinedRouting",
        ], key="az_outbound")
        ip_family = st.selectbox("IP Family",
                                  ["IPv4", "DualStackIPv4Primary", "DualStackIPv6Primary"],
                                  key="az_ipf")

    net_rg = st.text_input("Network Resource Group Name", key="az_net_rg")
    vnet = st.text_input("Virtual Network", key="az_vnet")

    user_tags_str = st.text_input("User Tags (key=value, comma separated)", key="az_tags")
    user_tags = {}
    if user_tags_str:
        for pair in user_tags_str.split(","):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                user_tags[k.strip()] = v.strip()

    platform = {"region": region}
    if cloud_name != "AzurePublicCloud":
        platform["cloudName"] = cloud_name
    if outbound != "Loadbalancer":
        platform["outboundType"] = outbound
    if ip_family != "IPv4":
        platform["ipFamily"] = ip_family
    if base_rg:
        platform["baseDomainResourceGroupName"] = base_rg
    if rg_name:
        platform["resourceGroupName"] = rg_name
    if net_rg:
        platform["networkResourceGroupName"] = net_rg
    if vnet:
        platform["virtualNetwork"] = vnet
    if user_tags:
        platform["userTags"] = user_tags

    return platform, _render_azure_machinepool()


def _render_azure_machinepool():
    st.markdown("**Azure Machine Pool Defaults**")
    col1, col2 = st.columns(2)
    with col1:
        instance_type = st.text_input("Instance Type", key="az_inst")
        zones_str = st.text_input("Zones (comma separated)", key="az_zones")
    with col2:
        disk_size = st.number_input("OS Disk Size (GB)", value=128, min_value=1,
                                     key="az_disk_size")
        disk_type = st.selectbox("OS Disk Type",
                                  ["Premium_LRS", "StandardSSD_LRS", "Standard_LRS"],
                                  key="az_disk_type")
    mp = {}
    if instance_type:
        mp["type"] = instance_type
    if zones_str:
        mp["zones"] = [z.strip() for z in zones_str.split(",") if z.strip()]
    mp["osDisk"] = {"diskSizeGB": disk_size, "diskType": disk_type}
    return mp


def render_gcp():
    st.subheader("GCP Configuration")
    col1, col2 = st.columns(2)
    with col1:
        project_id = st.text_input("Project ID *", key="gcp_project")
        region = st.text_input("Region *", value="us-central1", key="gcp_region")
    with col2:
        network = st.text_input("Network", key="gcp_network")
        network_project = st.text_input("Network Project ID", key="gcp_net_proj")

    col1, col2 = st.columns(2)
    with col1:
        cp_subnet = st.text_input("Control Plane Subnet", key="gcp_cp_subnet")
    with col2:
        compute_subnet = st.text_input("Compute Subnet", key="gcp_comp_subnet")

    labels_str = st.text_input("User Labels (key=value, comma separated)", key="gcp_labels")
    user_labels = []
    if labels_str:
        for pair in labels_str.split(","):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                user_labels.append({"key": k.strip(), "value": v.strip()})

    platform = {"projectID": project_id, "region": region}
    if network:
        platform["network"] = network
    if network_project:
        platform["networkProjectID"] = network_project
    if cp_subnet:
        platform["controlPlaneSubnet"] = cp_subnet
    if compute_subnet:
        platform["computeSubnet"] = compute_subnet
    if user_labels:
        platform["userLabels"] = user_labels

    return platform, _render_gcp_machinepool()


def _render_gcp_machinepool():
    st.markdown("**GCP Machine Pool Defaults**")
    col1, col2 = st.columns(2)
    with col1:
        instance_type = st.text_input("Instance Type", key="gcp_inst")
        zones_str = st.text_input("Zones (comma separated)", key="gcp_zones")
    with col2:
        disk_size = st.number_input("OS Disk Size (GB)", value=128, min_value=16,
                                     max_value=65536, key="gcp_disk_size")
        disk_type = st.selectbox("OS Disk Type",
                                  ["pd-ssd", "pd-balanced", "pd-standard",
                                   "hyperdisk-balanced"],
                                  key="gcp_disk_type")
    secure_boot = st.selectbox("Secure Boot", ["", "Enabled", "Disabled"], key="gcp_sb")

    mp = {}
    if instance_type:
        mp["type"] = instance_type
    if zones_str:
        mp["zones"] = [z.strip() for z in zones_str.split(",") if z.strip()]
    mp["osDisk"] = {"DiskSizeGB": disk_size, "diskType": disk_type}
    if secure_boot:
        mp["secureBoot"] = secure_boot
    return mp


def render_vsphere():
    st.subheader("vSphere Configuration")

    st.markdown("**vCenter**")
    col1, col2 = st.columns(2)
    with col1:
        server = st.text_input("vCenter Server *", key="vs_server")
        username = st.text_input("Username *", key="vs_user")
    with col2:
        port = st.number_input("Port", value=443, min_value=1, max_value=32767, key="vs_port")
        password = st.text_input("Password *", type="password", key="vs_pass")

    datacenters_str = st.text_input("Datacenters * (comma separated)", key="vs_dc")
    datacenters = ([d.strip() for d in datacenters_str.split(",") if d.strip()]
                   if datacenters_str else [])

    st.markdown("**Failure Domain**")
    col1, col2, col3 = st.columns(3)
    with col1:
        fd_name = st.text_input("Name", value="default", key="vs_fd_name")
        fd_region = st.text_input("Region", value="default-region", key="vs_fd_region")
        fd_zone = st.text_input("Zone", value="default-zone", key="vs_fd_zone")
    with col2:
        fd_dc = st.text_input("Datacenter *", key="vs_fd_dc")
        fd_cluster = st.text_input("Compute Cluster *", key="vs_fd_cluster")
        fd_datastore = st.text_input("Datastore *", key="vs_fd_ds")
    with col3:
        fd_network = st.text_input("Network *", key="vs_fd_net")
        fd_resource_pool = st.text_input("Resource Pool", key="vs_fd_rp")
        fd_folder = st.text_input("Folder", key="vs_fd_folder")

    disk_type = st.selectbox("Disk Type", ["thin", "thick", "eagerZeroedThick"], key="vs_dt")

    col1, col2 = st.columns(2)
    with col1:
        api_vips = st.text_input("API VIPs (comma separated)", key="vs_api_vip")
    with col2:
        ingress_vips = st.text_input("Ingress VIPs (comma separated)", key="vs_ing_vip")

    vcenter = {"server": server, "user": username, "password": password,
               "datacenters": datacenters}
    if port != 443:
        vcenter["port"] = port

    topology = {
        "datacenter": fd_dc,
        "computeCluster": fd_cluster,
        "datastore": fd_datastore,
        "networks": [fd_network] if fd_network else [],
    }
    if fd_resource_pool:
        topology["resourcePool"] = fd_resource_pool
    if fd_folder:
        topology["folder"] = fd_folder

    platform = {
        "vcenters": [vcenter],
        "failureDomains": [{
            "name": fd_name, "region": fd_region, "zone": fd_zone,
            "server": server, "topology": topology,
        }],
    }
    if disk_type != "thin":
        platform["diskType"] = disk_type
    if api_vips:
        platform["apiVIPs"] = [v.strip() for v in api_vips.split(",") if v.strip()]
    if ingress_vips:
        platform["ingressVIPs"] = [v.strip() for v in ingress_vips.split(",") if v.strip()]

    return platform, _render_vsphere_machinepool()


def _render_vsphere_machinepool():
    st.markdown("**vSphere Machine Pool Defaults**")
    col1, col2 = st.columns(2)
    with col1:
        cpus = st.number_input("CPUs", value=4, min_value=1, key="vs_cpus")
        cores = st.number_input("Cores Per Socket", value=2, min_value=1, key="vs_cores")
    with col2:
        memory = st.number_input("Memory (MiB)", value=16384, min_value=1024, key="vs_mem")
        disk_size = st.number_input("OS Disk Size (GB)", value=120, min_value=1, key="vs_disk")
    return {
        "cpus": cpus, "coresPerSocket": cores,
        "memoryMB": memory, "osDisk": {"diskSizeGB": disk_size},
    }


def render_baremetal():
    st.subheader("Bare Metal Configuration")
    col1, col2 = st.columns(2)
    with col1:
        api_vips = st.text_input("API VIPs * (comma separated)", key="bm_api_vip")
        prov_network = st.selectbox("Provisioning Network",
                                     ["Managed", "Unmanaged", "Disabled"], key="bm_prov_net")
        prov_interface = st.text_input("Provisioning Network Interface *", key="bm_prov_iface")
    with col2:
        ingress_vips = st.text_input("Ingress VIPs * (comma separated)", key="bm_ing_vip")
        prov_cidr = st.text_input("Provisioning Network CIDR", key="bm_prov_cidr")
        bootstrap_prov_ip = st.text_input("Bootstrap Provisioning IP", key="bm_bstrap_ip")

    platform = {"provisioningNetworkInterface": prov_interface or ""}
    if prov_network != "Managed":
        platform["provisioningNetwork"] = prov_network
    if api_vips:
        platform["apiVIPs"] = [v.strip() for v in api_vips.split(",") if v.strip()]
    if ingress_vips:
        platform["ingressVIPs"] = [v.strip() for v in ingress_vips.split(",") if v.strip()]
    if prov_cidr:
        platform["provisioningNetworkCIDR"] = prov_cidr
    if bootstrap_prov_ip:
        platform["bootstrapProvisioningIP"] = bootstrap_prov_ip

    st.markdown("**Hosts**")
    num_hosts = st.number_input("Number of Hosts", value=3, min_value=1, max_value=50,
                                 key="bm_nhosts")
    hosts = []
    for i in range(num_hosts):
        with st.expander(f"Host {i + 1}", expanded=i == 0):
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("Name *", key=f"bm_h{i}_name")
                role = st.selectbox("Role", ["control-plane", "compute"],
                                     key=f"bm_h{i}_role")
                boot_mac = st.text_input("Boot MAC Address *", key=f"bm_h{i}_mac")
                boot_mode = st.selectbox("Boot Mode", ["UEFI", "UEFISecureBoot", "legacy"],
                                          key=f"bm_h{i}_bm")
            with col2:
                bmc_addr = st.text_input("BMC Address *", key=f"bm_h{i}_bmc_addr")
                bmc_user = st.text_input("BMC Username *", key=f"bm_h{i}_bmc_user")
                bmc_pass = st.text_input("BMC Password *", type="password",
                                          key=f"bm_h{i}_bmc_pass")
            hosts.append({
                "name": name, "role": role,
                "bootMACAddress": boot_mac, "bootMode": boot_mode,
                "bmc": {
                    "address": bmc_addr, "username": bmc_user, "password": bmc_pass,
                    "disableCertificateVerification": True,
                },
            })

    platform["hosts"] = hosts
    return platform, {}


def render_openstack():
    st.subheader("OpenStack Configuration")
    col1, col2 = st.columns(2)
    with col1:
        cloud = st.text_input("Cloud Name * (from clouds.yaml)", key="os_cloud")
        ext_network = st.text_input("External Network", key="os_ext_net")
        api_fip = st.text_input("API Floating IP", key="os_api_fip")
    with col2:
        ingress_fip = st.text_input("Ingress Floating IP", key="os_ing_fip")
        os_image = st.text_input("Cluster OS Image", key="os_image")
        ext_dns_str = st.text_input("External DNS (comma separated)", key="os_ext_dns")

    col1, col2 = st.columns(2)
    with col1:
        api_vips = st.text_input("API VIPs (comma separated)", key="os_api_vip")
    with col2:
        ingress_vips = st.text_input("Ingress VIPs (comma separated)", key="os_ing_vip")

    ext_dns = [d.strip() for d in ext_dns_str.split(",") if d.strip()] if ext_dns_str else []

    platform = {"cloud": cloud, "externalDNS": ext_dns}
    if ext_network:
        platform["externalNetwork"] = ext_network
    if api_fip:
        platform["apiFloatingIP"] = api_fip
    if ingress_fip:
        platform["ingressFloatingIP"] = ingress_fip
    if os_image:
        platform["clusterOSImage"] = os_image
    if api_vips:
        platform["apiVIPs"] = [v.strip() for v in api_vips.split(",") if v.strip()]
    if ingress_vips:
        platform["ingressVIPs"] = [v.strip() for v in ingress_vips.split(",") if v.strip()]

    return platform, _render_openstack_machinepool()


def _render_openstack_machinepool():
    st.markdown("**OpenStack Machine Pool Defaults**")
    col1, col2 = st.columns(2)
    with col1:
        flavor = st.text_input("Flavor Name", key="os_flavor")
    with col2:
        server_group = st.selectbox("Server Group Policy",
                                     ["soft-anti-affinity", "anti-affinity",
                                      "soft-affinity", "affinity"],
                                     key="os_sgp")
    mp = {}
    if flavor:
        mp["type"] = flavor
    if server_group != "soft-anti-affinity":
        mp["serverGroupPolicy"] = server_group
    return mp


def render_nutanix():
    st.subheader("Nutanix Configuration")

    st.markdown("**Prism Central**")
    col1, col2 = st.columns(2)
    with col1:
        pc_address = st.text_input("Address *", key="nx_pc_addr")
        pc_port = st.number_input("Port *", value=9440, min_value=1, key="nx_pc_port")
    with col2:
        pc_user = st.text_input("Username *", key="nx_pc_user")
        pc_pass = st.text_input("Password *", type="password", key="nx_pc_pass")

    st.markdown("**Prism Element**")
    col1, col2 = st.columns(2)
    with col1:
        pe_uuid = st.text_input("UUID *", key="nx_pe_uuid")
        pe_name = st.text_input("Name", key="nx_pe_name")
    with col2:
        pe_address = st.text_input("Endpoint Address", key="nx_pe_addr")
        pe_port = st.number_input("Endpoint Port", value=9440, min_value=1, key="nx_pe_port")

    subnet_str = st.text_input("Subnet UUIDs * (comma separated)", key="nx_subnets")
    subnet_uuids = ([s.strip() for s in subnet_str.split(",") if s.strip()]
                    if subnet_str else [])

    col1, col2 = st.columns(2)
    with col1:
        api_vips = st.text_input("API VIPs (comma separated)", key="nx_api_vip")
    with col2:
        ingress_vips = st.text_input("Ingress VIPs (comma separated)", key="nx_ing_vip")

    pe = {"uuid": pe_uuid}
    if pe_name:
        pe["name"] = pe_name
    if pe_address:
        pe["endpoint"] = {"address": pe_address, "port": pe_port}

    platform = {
        "prismCentral": {
            "endpoint": {"address": pc_address, "port": pc_port},
            "username": pc_user, "password": pc_pass,
        },
        "prismElements": [pe],
        "subnetUUIDs": subnet_uuids,
    }
    if api_vips:
        platform["apiVIPs"] = [v.strip() for v in api_vips.split(",") if v.strip()]
    if ingress_vips:
        platform["ingressVIPs"] = [v.strip() for v in ingress_vips.split(",") if v.strip()]

    return platform, _render_nutanix_machinepool()


def _render_nutanix_machinepool():
    st.markdown("**Nutanix Machine Pool Defaults**")
    col1, col2 = st.columns(2)
    with col1:
        cpus = st.number_input("CPUs", value=4, min_value=1, key="nx_cpus")
        cores = st.number_input("Cores Per Socket", value=1, min_value=1, key="nx_cores")
    with col2:
        memory = st.number_input("Memory (MiB)", value=16384, min_value=1024, key="nx_mem")
        disk_size = st.number_input("OS Disk Size (GiB)", value=120, min_value=1, key="nx_disk")
    boot_type = st.selectbox("Boot Type", ["", "Legacy", "UEFI", "SecureBoot"], key="nx_boot")

    mp = {
        "cpus": cpus, "coresPerSocket": cores,
        "memoryMiB": memory, "osDisk": {"diskSizeGiB": disk_size},
    }
    if boot_type:
        mp["bootType"] = boot_type
    return mp


def render_ibmcloud():
    st.subheader("IBM Cloud Configuration")
    col1, col2 = st.columns(2)
    with col1:
        region = st.text_input("Region *", key="ibm_region")
        rg_name = st.text_input("Resource Group Name", key="ibm_rg")
        vpc_name = st.text_input("VPC Name", key="ibm_vpc")
    with col2:
        net_rg = st.text_input("Network Resource Group Name", key="ibm_net_rg")
        cp_subnets_str = st.text_input("Control Plane Subnets (comma sep)", key="ibm_cp_sub")
        comp_subnets_str = st.text_input("Compute Subnets (comma sep)", key="ibm_comp_sub")

    cp_subnets = ([s.strip() for s in cp_subnets_str.split(",") if s.strip()]
                  if cp_subnets_str else [])
    comp_subnets = ([s.strip() for s in comp_subnets_str.split(",") if s.strip()]
                    if comp_subnets_str else [])

    platform = {"region": region}
    if rg_name:
        platform["resourceGroupName"] = rg_name
    if vpc_name:
        platform["vpcName"] = vpc_name
    if net_rg:
        platform["networkResourceGroupName"] = net_rg
    if cp_subnets:
        platform["controlPlaneSubnets"] = cp_subnets
    if comp_subnets:
        platform["computeSubnets"] = comp_subnets

    return platform, _render_ibmcloud_machinepool()


def _render_ibmcloud_machinepool():
    st.markdown("**IBM Cloud Machine Pool Defaults**")
    col1, col2 = st.columns(2)
    with col1:
        instance_type = st.text_input("Instance Type", key="ibm_inst")
    with col2:
        zones_str = st.text_input("Zones (comma separated)", key="ibm_zones")
    mp = {}
    if instance_type:
        mp["type"] = instance_type
    if zones_str:
        mp["zones"] = [z.strip() for z in zones_str.split(",") if z.strip()]
    return mp


def render_powervs():
    st.subheader("PowerVS Configuration")
    col1, col2 = st.columns(2)
    with col1:
        rg = st.text_input("PowerVS Resource Group *", key="pvs_rg")
        zone = st.text_input("Zone *", key="pvs_zone")
        region = st.text_input("Region", key="pvs_region")
    with col2:
        user_id = st.text_input("User ID *", key="pvs_uid")
        vpc_region = st.text_input("VPC Region", key="pvs_vpc_region")
        vpc_name = st.text_input("VPC Name", key="pvs_vpc_name")

    svc_guid = st.text_input("Service Instance GUID", key="pvs_guid")
    subnets_str = st.text_input("VPC Subnets (comma separated)", key="pvs_subnets")
    subnets = [s.strip() for s in subnets_str.split(",") if s.strip()] if subnets_str else []

    platform = {"powervsResourceGroup": rg, "zone": zone, "userID": user_id}
    if region:
        platform["region"] = region
    if vpc_region:
        platform["vpcRegion"] = vpc_region
    if vpc_name:
        platform["vpcName"] = vpc_name
    if svc_guid:
        platform["serviceInstanceGUID"] = svc_guid
    if subnets:
        platform["vpcSubnets"] = subnets

    return platform, _render_powervs_machinepool()


def _render_powervs_machinepool():
    st.markdown("**PowerVS Machine Pool Defaults**")
    col1, col2 = st.columns(2)
    with col1:
        memory = st.number_input("Memory (GiB)", value=32, min_value=1, key="pvs_mem")
        proc_type = st.selectbox("Processor Type", ["Shared", "Dedicated", "Capped"],
                                  key="pvs_proc")
    with col2:
        processors = st.text_input("Processors", value="0.5", key="pvs_procs")
        sys_type = st.text_input("System Type", key="pvs_sys")
    mp = {}
    if memory:
        mp["memoryGiB"] = memory
    if proc_type:
        mp["procType"] = proc_type
    if processors:
        mp["processors"] = processors
    if sys_type:
        mp["sysType"] = sys_type
    return mp


def render_none_platform():
    st.subheader("None Platform")
    st.info("The 'none' platform requires no additional configuration. "
            "You must provide your own infrastructure.")
    return {}, {}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

PLATFORM_RENDERERS = {
    "aws": render_aws,       "azure": render_azure,
    "gcp": render_gcp,       "vsphere": render_vsphere,
    "baremetal": render_baremetal,  "openstack": render_openstack,
    "nutanix": render_nutanix,     "ibmcloud": render_ibmcloud,
    "powervs": render_powervs,     "none": render_none_platform,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="OpenShift Install Config Creator",
        page_icon=":wrench:",
        layout="wide",
    )
    st.title("OpenShift Install Config Creator")
    st.caption("Generate install-config.yaml files for OpenShift clusters")

    st.markdown("""<style>
    [data-testid="stTextArea"] textarea, .stTextArea textarea {
        font-family: 'SF Mono', 'Fira Code', Consolas, 'Liberation Mono', Menlo,
                     monospace !important;
        font-size: 15px !important;
        line-height: 1.6 !important;
    }
    </style>""", unsafe_allow_html=True)

    # --- YAML -> Form sync (must run before any widget is instantiated) ------
    if st.session_state.get("yaml_manual_edit") and "yaml_edit" in st.session_state:
        current_yaml = st.session_state["yaml_edit"]
        needs_sync = st.session_state.pop("_yaml_needs_sync", False)
        if needs_sync or current_yaml != st.session_state.get("_yaml_last_synced", ""):
            sync_yaml_to_form(current_yaml)
            st.session_state["_yaml_last_synced"] = current_yaml

    # --- Sidebar -------------------------------------------------------------
    with st.sidebar:
        st.header("Platform")
        platform_name = st.selectbox(
            "Select Platform", PLATFORMS, key="platform_select",
            format_func=lambda x: PLATFORM_LABELS.get(x, x),
        )
        st.divider()
        st.markdown("**Quick Info**")
        st.markdown(f"- Platform: `{platform_name}`")
        st.markdown("- API Version: `v1`")
        st.markdown("- Fields marked with * are required")

    # --- Two-column layout ---------------------------------------------------
    left, right = st.columns([3, 2])

    with left:
        config = render_common_fields()
        st.divider()
        platform_config, mp_defaults = PLATFORM_RENDERERS[platform_name]()
        config["platform"] = {platform_name: platform_config}
        if mp_defaults:
            config["controlPlane"]["platform"] = {platform_name: mp_defaults}
            config["compute"][0]["platform"] = {platform_name: mp_defaults}

    with right:
        st.header("Generated YAML")
        yaml_output = render_yaml(config)

        if "yaml_manual_edit" not in st.session_state:
            st.session_state.yaml_manual_edit = False

        def _mark_manual_edit():
            st.session_state.yaml_manual_edit = True

        if not st.session_state.yaml_manual_edit:
            st.session_state["yaml_edit"] = yaml_output

        if st.session_state.yaml_manual_edit:
            col_sync, col_apply = st.columns(2)
            with col_sync:
                if st.button("Sync from Form",
                             help="Reset editor to match current form values"):
                    st.session_state.yaml_manual_edit = False
                    st.session_state["yaml_edit"] = yaml_output
                    st.session_state.pop("_yaml_last_synced", None)
                    st.rerun()
            with col_apply:
                if st.button("Apply to Form",
                             help="Update form fields to match the YAML editor"):
                    st.session_state["_yaml_needs_sync"] = True
                    st.rerun()

        edited_yaml = st.text_area(
            "YAML Editor", value=yaml_output, height=600, key="yaml_edit",
            on_change=_mark_manual_edit,
            help="Edit directly or add fields not covered by the form. "
                 "Use camelCase keys (e.g. baseDomain, networkType). "
                 "Click outside the editor, then 'Apply to Form' to sync.",
        )
        if not edited_yaml:
            edited_yaml = yaml_output

        result = validate_yaml(edited_yaml)
        if result["valid"] and not result["warnings"]:
            st.success("Valid install-config")
        for err in result["errors"]:
            st.error(err)
        for warn in result["warnings"]:
            st.warning(warn)

        custom_filename = st.text_input("Filename", value="install-config.yaml",
                                        key="dl_filename")
        st.download_button(label="Download", data=edited_yaml,
                           file_name=custom_filename, mime="text/yaml")


if __name__ == "__main__":
    main()
