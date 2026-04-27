// メイン Bicep テンプレート — 旅行マーケティング AI パイプライン
// azd up で Azure リソースをプロビジョニングする

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('環境名（リソース名の接頭辞に使用）')
param environmentName string

@minLength(1)
@description('Azure リージョン')
param location string

@description('コンテナイメージ名（azd が自動設定する）')
param imageName string = ''

@description('Container Apps Environment を VNet 統合で作成する。既存の非 VNet 統合 CAE では replacement / blue-green 移行の承認後に true にする。')
param enableContainerAppsVnetIntegration bool = false

@description('既存 CAE の VNet 統合移行を明示承認する確認文字列。既存環境では ENABLE_CONTAINER_APPS_VNET_INTEGRATION=true と併せて CONFIRM_CAE_VNET_MIGRATION を設定する。')
param containerAppsVnetIntegrationMigrationApproval string = ''

@minValue(1)
@maxValue(10)
@description('Container App の最大 replica 数。Cosmos DB private endpoint 経路の疎通確認後に 2 以上へ引き上げる。')
param containerAppMaxReplicas int = 1

@description('Voice Live SPA アプリの Client ID（postprovision で設定）')
param voiceSpaClientId string = ''

@secure()
@description('上司承認 workflow の HTTP trigger URL（Teams 対応 workflow を手動構成して設定）')
param managerApprovalTriggerUrl string = ''

@description('承認後通知に使う Microsoft Teams connection resource 名（例: teams-1）。空なら Teams チャネル通知は無効')
param postApprovalTeamsConnectionName string = ''

@description('承認後通知先の Team ID。空なら Teams チャネル通知は無効')
param postApprovalTeamsTeamId string = ''

@description('承認後通知先の Channel ID。空なら Teams チャネル通知は無効')
param postApprovalTeamsChannelId string = ''

@description('承認後に成果物を保存する SharePoint site ID。空なら SharePoint 保存は無効')
param postApprovalSharePointSiteId string = ''

@description('MAI-Image-2 用の別 Azure AI / Foundry アカウント endpoint（任意）')
param imageProjectEndpointMai string = ''

@description('MAI-Image-2 用の別 Azure AI / Foundry アカウント名（同一 RG 前提、任意）')
param maiResourceName string = ''

var abbrs = loadJsonContent('abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
}
var apimName = '${abbrs.apim}${resourceToken}'
var defaultModelDeploymentName = 'gpt-5-4-mini'
var defaultImageModelDeploymentName = 'gpt-image-1.5'
var aiServicesApiBase = 'https://${abbrs.aiFoundry}${resourceToken}.services.ai.azure.com'
var improvementMcpEndpoint = 'https://${apimName}.azure-api.net/improvement-mcp/runtime/webhooks/mcp'
var containerAppsVnetIntegrationApproved = enableContainerAppsVnetIntegration && containerAppsVnetIntegrationMigrationApproval == 'CONFIRM_CAE_VNET_MIGRATION'

// リソースグループ
resource rg 'Microsoft.Resources/resourceGroups@2024-07-01' = {
  name: '${abbrs.resourceGroup}${environmentName}'
  location: location
  tags: tags
}

// Log Analytics ワークスペース
module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'log-analytics'
  scope: rg
  params: {
    name: '${abbrs.logAnalytics}${resourceToken}'
    location: location
    tags: tags
  }
}

// Application Insights
module appInsights 'modules/app-insights.bicep' = {
  name: 'app-insights'
  scope: rg
  params: {
    name: '${abbrs.appInsights}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
  }
}

// Container Registry
module acr 'modules/container-registry.bicep' = {
  name: 'container-registry'
  scope: rg
  params: {
    name: '${abbrs.containerRegistry}${resourceToken}'
    location: location
    tags: tags
  }
}

// Key Vault
module keyVault 'modules/key-vault.bicep' = {
  name: 'key-vault'
  scope: rg
  params: {
    name: '${abbrs.keyVault}${resourceToken}'
    location: location
    tags: tags
    privateEndpointsSubnetId: vnet.outputs.privateEndpointsSubnetId
    vnetId: vnet.outputs.id
  }
}

// Microsoft Foundry リソース（AI Services + model deployment）
module aiFoundry 'modules/ai-services.bicep' = {
  name: 'ai-foundry'
  scope: rg
  params: {
    name: '${abbrs.aiFoundry}${resourceToken}'
    location: location
    tags: tags
    modelDeploymentName: defaultModelDeploymentName
    imageModelDeploymentName: defaultImageModelDeploymentName
  }
}

// Foundry Project
module aiProject 'modules/ai-project.bicep' = {
  name: 'ai-project'
  scope: rg
  params: {
    name: '${abbrs.aiProject}${resourceToken}'
    location: location
    tags: tags
    aiFoundryName: aiFoundry.outputs.name
  }
}

var aiProjectEndpoint = 'https://${aiFoundry.outputs.name}.services.ai.azure.com/api/projects/${aiProject.outputs.name}'

// VNet（Container Apps + Private Endpoints）
// 注: 既存の CAE には VNet を追加できないため、新規デプロイ時のみ有効
module vnet 'modules/vnet.bicep' = {
  name: 'vnet'
  scope: rg
  params: {
    name: 'vnet-${resourceToken}'
    location: location
    tags: tags
  }
}

// Container Apps Environment
// VNet 統合は新規作成時のみ適用（既存 CAE への追加は不可）。
// 既存の非 VNet 統合 CAE から移行する場合は、CAE/Container App の再作成または blue-green 移行の承認が必要。
module containerAppsEnv 'modules/container-apps-env.bicep' = {
  name: 'container-apps-env'
  scope: rg
  params: {
    name: '${abbrs.containerAppsEnv}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
    subnetId: containerAppsVnetIntegrationApproved ? vnet.outputs.containerAppsSubnetId : ''
  }
}

// Container App
module containerApp 'modules/container-app.bicep' = {
  name: 'container-app'
  scope: rg
  params: {
    name: '${abbrs.containerApp}${resourceToken}'
    location: location
    tags: tags
    containerAppsEnvironmentId: containerAppsEnv.outputs.id
    containerRegistryName: acr.outputs.name
    imageName: !empty(imageName) ? imageName : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
    keyVaultName: keyVault.outputs.name
    appInsightsConnectionString: appInsights.outputs.connectionString
    modelName: defaultModelDeploymentName
    projectEndpoint: aiProjectEndpoint
    imageProjectEndpointMai: imageProjectEndpointMai
    cosmosDbEndpoint: cosmosDb.outputs.endpoint

    contentUnderstandingEndpoint: aiServicesApiBase
    speechServiceEndpoint: aiFoundry.outputs.endpoint
    speechServiceRegion: location
    logicAppCallbackUrl: logicApp.outputs.callbackUrl
    managerApprovalTriggerUrl: managerApprovalTriggerUrl
    voiceSpaClientId: voiceSpaClientId
    tenantId: tenant().tenantId
    improvementMcpEndpoint: improvementMcpEndpoint
    maxReplicas: containerAppMaxReplicas
  }
}

// Allow the application managed identity to call the Foundry resource
module aiFoundryAppAccess 'modules/ai-project-app-access.bicep' = {
  name: 'ai-foundry-app-access'
  scope: rg
  params: {
    aiAccountName: aiFoundry.outputs.name
    principalId: containerApp.outputs.principalId
  }
}

// Optional: grant the app MI access to a separate MAI account used for MAI-Image-2
module maiResourceAppAccess 'modules/ai-project-app-access.bicep' = if (!empty(maiResourceName)) {
  name: 'mai-resource-app-access'
  scope: rg
  params: {
    aiAccountName: maiResourceName
    principalId: containerApp.outputs.principalId
  }
}

// Azure API Management (AI Gateway)
module apim 'modules/api-management.bicep' = {
  name: 'api-management'
  scope: rg
  params: {
    name: apimName
    location: location
    tags: tags
    appInsightsId: appInsights.outputs.id
    appInsightsInstrumentationKey: appInsights.outputs.instrumentationKey
  }
}

// APIM MI に Foundry へのアクセス権を付与
module aiFoundryApimAccess 'modules/ai-project-app-access.bicep' = {
  name: 'ai-foundry-apim-access'
  scope: rg
  params: {
    aiAccountName: aiFoundry.outputs.name
    principalId: apim.outputs.principalId
  }
}

// Logic Apps (承認後自動アクション)
module logicApp 'modules/logic-app.bicep' = {
  name: 'logic-app'
  scope: rg
  params: {
    name: '${abbrs.logicApp}${resourceToken}'
    location: location
    teamsConnectionName: postApprovalTeamsConnectionName
    teamsTargetTeamId: postApprovalTeamsTeamId
    teamsTargetChannelId: postApprovalTeamsChannelId
    sharePointSiteId: postApprovalSharePointSiteId
    tags: tags
  }
}

// Azure Cosmos DB (会話履歴永続化)
module cosmosDb 'modules/cosmos-db.bicep' = {
  name: 'cosmos-db'
  scope: rg
  params: {
    name: '${abbrs.cosmosDb}${resourceToken}'
    location: location
    tags: tags
    privateEndpointsSubnetId: vnet.outputs.privateEndpointsSubnetId
    vnetId: vnet.outputs.id
  }
}

// Cosmos DB RBAC: Container App MI に読み書きアクセス
module cosmosDbAccess 'modules/cosmos-db-access.bicep' = {
  name: 'cosmos-db-access'
  scope: rg
  params: {
    cosmosAccountName: cosmosDb.outputs.name
    principalId: containerApp.outputs.principalId
  }
}

// 出力
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.outputs.name
output AZURE_CONTAINER_APP_NAME string = containerApp.outputs.name
output AZURE_AI_PROJECT_ENDPOINT string = aiProjectEndpoint
output AZURE_AI_PROJECT_NAME string = aiProject.outputs.name
output AZURE_AI_FOUNDRY_NAME string = aiFoundry.outputs.name
output MODEL_NAME string = defaultModelDeploymentName
output IMAGE_MODEL_NAME string = defaultImageModelDeploymentName
output AZURE_APIM_NAME string = apim.outputs.name
output AZURE_APIM_GATEWAY_URL string = apim.outputs.gatewayUrl
output IMPROVEMENT_MCP_ENDPOINT string = improvementMcpEndpoint
output AZURE_LOGIC_APP_NAME string = logicApp.outputs.name
output AZURE_RESOURCE_GROUP string = rg.name
output COSMOS_DB_ENDPOINT string = cosmosDb.outputs.endpoint
output SERVICE_WEB_ENDPOINTS array = [containerApp.outputs.uri]
