// Azure API Management (AI Gateway)
// Foundry AI Gateway として統合 — モデルデプロイメントの一元管理・監視・負荷分散
// 参照: https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities
// 参照: https://learn.microsoft.com/azure/foundry/configuration/enable-ai-api-management-gateway-portal

param name string
param location string
param tags object = {}
param publisherEmail string = 'team-d@hackathon.local'
param publisherName string = 'Team D Hackathon'
param appInsightsId string
param appInsightsInstrumentationKey string
param aiServicesId string = ''

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'StandardV2'
    capacity: 1
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

// Application Insights ログ統合
resource apimLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' = {
  parent: apim
  name: 'appinsights-logger'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsightsId
    credentials: {
      instrumentationKey: appInsightsInstrumentationKey
    }
  }
}

// --- APIM に Cognitive Services User ロールを付与 ---
// Managed Identity でバックエンドの Foundry モデルにアクセスするために必要
resource cognitiveServicesUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(aiServicesId)) {
  name: guid(apim.id, 'CognitiveServicesUser', aiServicesId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output id string = apim.id
output name string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
output principalId string = apim.identity.principalId
