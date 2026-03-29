// Azure API Management (AI Gateway)
// Container Apps ↔ Foundry 間のリバースプロキシとして配置

param name string
param location string
param tags object = {}
param publisherEmail string = 'team-d@hackathon.local'
param publisherName string = 'Team D Hackathon'
param appInsightsId string
param appInsightsInstrumentationKey string
param foundryEndpoint string = ''

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'BasicV2'
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

// Foundry API バックエンド定義
resource foundryBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = if (!empty(foundryEndpoint)) {
  parent: apim
  name: 'foundry-backend'
  properties: {
    protocol: 'http'
    url: foundryEndpoint
    credentials: {
      header: {}
    }
    tls: {
      validateCertificateChain: true
      validateCertificateName: true
    }
  }
}

// AI Gateway API 定義
resource aiGatewayApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = if (!empty(foundryEndpoint)) {
  parent: apim
  name: 'foundry-ai-gateway'
  properties: {
    displayName: 'Foundry AI Gateway'
    path: 'ai'
    protocols: ['https']
    subscriptionRequired: false
    serviceUrl: foundryEndpoint
  }
}

// トークン制限ポリシー（グローバル）
resource globalPolicy 'Microsoft.ApiManagement/service/policies@2024-06-01-preview' = {
  parent: apim
  name: 'policy'
  properties: {
    format: 'xml'
    value: '''
      <policies>
        <inbound>
          <base />
          <rate-limit calls="60" renewal-period="60" />
          <set-header name="X-Request-Id" exists-action="skip">
            <value>@(context.RequestId.ToString())</value>
          </set-header>
        </inbound>
        <backend>
          <base />
        </backend>
        <outbound>
          <base />
          <set-header name="X-Gateway" exists-action="override">
            <value>travel-marketing-ai-gateway</value>
          </set-header>
        </outbound>
        <on-error>
          <base />
        </on-error>
      </policies>
    '''
  }
}

output id string = apim.id
output name string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
output principalId string = apim.identity.principalId
