"""Static IaC checks for private Container Apps to Cosmos connectivity."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_container_apps_environment_uses_dedicated_vnet_subnet() -> None:
    main = _read("infra/main.bicep")
    vnet = _read("infra/modules/vnet.bicep")
    env = _read("infra/modules/container-apps-env.bicep")
    parameters = _read("infra/main.parameters.json")

    assert "param enableContainerAppsVnetIntegration bool = false" in main
    assert "param containerAppsVnetIntegrationMigrationApproval string = ''" in main
    assert (
        "var containerAppsVnetIntegrationApproved = enableContainerAppsVnetIntegration "
        "&& containerAppsVnetIntegrationMigrationApproval == 'CONFIRM_CAE_VNET_MIGRATION'"
    ) in main
    assert "subnetId: containerAppsVnetIntegrationApproved ? vnet.outputs.containerAppsSubnetId : ''" in main
    assert "ENABLE_CONTAINER_APPS_VNET_INTEGRATION=false" in parameters
    assert "CONTAINER_APPS_VNET_INTEGRATION_MIGRATION_APPROVAL=" in parameters
    assert "name: 'snet-container-apps'" in vnet
    assert "serviceName: 'Microsoft.App/environments'" in vnet
    assert "infrastructureSubnetId: subnetId" in env


def test_cosmos_private_endpoint_dns_and_public_network_are_locked_down() -> None:
    cosmos = _read("infra/modules/cosmos-db.bicep")

    assert "publicNetworkAccess: 'Disabled'" in cosmos
    assert "name: 'privatelink.documents.azure.com'" in cosmos
    assert "Microsoft.Network/privateDnsZones/virtualNetworkLinks" in cosmos
    assert "Microsoft.Network/privateEndpoints/privateDnsZoneGroups" in cosmos
    assert "privateDnsZoneId: privateDnsZone.id" in cosmos


def test_container_app_scale_out_stays_approval_controlled_until_private_path_verified() -> None:
    main = _read("infra/main.bicep")
    container_app = _read("infra/modules/container-app.bicep")
    parameters = _read("infra/main.parameters.json")

    assert "param containerAppMaxReplicas int = 1" in main
    assert "maxReplicas: containerAppMaxReplicas" in main
    assert "CONTAINER_APP_MAX_REPLICAS=1" in parameters
    assert "param maxReplicas int = 1" in container_app
    assert "maxReplicas: maxReplicas" in container_app
