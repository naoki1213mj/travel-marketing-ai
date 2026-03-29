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

var abbrs = loadJsonContent('abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
}
var defaultModelDeploymentName = 'gpt-5-4-mini'

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

// Container Apps Environment
module containerAppsEnv 'modules/container-apps-env.bicep' = {
  name: 'container-apps-env'
  scope: rg
  params: {
    name: '${abbrs.containerAppsEnv}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
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
    contentSafetyEndpoint: aiFoundry.outputs.endpoint
  }
}

// Allow the application managed identity to call the Foundry resource
module aiFoundryAppAccess 'modules/ai-project-app-access.bicep' = {
  name: 'ai-foundry-app-access'
  scope: rg
  params: {
    aiFoundryName: aiFoundry.outputs.name
    principalId: containerApp.outputs.principalId
  }
}

// Azure API Management (AI Gateway)
module apim 'modules/api-management.bicep' = {
  name: 'api-management'
  scope: rg
  params: {
    name: '${abbrs.apim}${resourceToken}'
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
    aiFoundryName: aiFoundry.outputs.name
    principalId: apim.outputs.principalId
  }
}

// Azure Functions MCP サーバー (Flex Consumption)
module functionApp 'modules/function-app.bicep' = {
  name: 'function-app'
  scope: rg
  params: {
    name: '${abbrs.functionApp}${resourceToken}'
    location: location
    tags: tags
    storageAccountName: '${abbrs.funcStorage}${resourceToken}'
    appInsightsConnectionString: appInsights.outputs.connectionString
  }
}

// Logic Apps (承認後自動アクション)
module logicApp 'modules/logic-app.bicep' = {
  name: 'logic-app'
  scope: rg
  params: {
    name: '${abbrs.logicApp}${resourceToken}'
    location: location
    tags: tags
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
output AZURE_APIM_GATEWAY_URL string = apim.outputs.gatewayUrl
output AZURE_FUNCTION_APP_NAME string = functionApp.outputs.name
output AZURE_LOGIC_APP_NAME string = logicApp.outputs.name
output AZURE_RESOURCE_GROUP string = rg.name
output CONTENT_SAFETY_ENDPOINT string = aiFoundry.outputs.endpoint
output SERVICE_WEB_ENDPOINTS array = [containerApp.outputs.uri]
