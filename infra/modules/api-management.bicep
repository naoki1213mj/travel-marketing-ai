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
param foundryEndpoint string = ''
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

// --- AI Gateway: Foundry バックエンド定義 ---
// Managed Identity 認証でバックエンドに接続（API キー不要）
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

// --- AI Gateway: Foundry API 定義 ---
// Azure OpenAI 互換エンドポイント + Azure AI Model Inference API
resource aiGatewayApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = if (!empty(foundryEndpoint)) {
  parent: apim
  name: 'foundry-ai-gateway'
  properties: {
    displayName: 'Foundry AI Gateway'
    description: 'Microsoft Foundry AI Gateway — モデルデプロイメントの一元管理'
    path: 'ai'
    protocols: ['https']
    subscriptionRequired: true
    serviceUrl: foundryEndpoint
    type: 'http'
  }
}

// --- AI Gateway: 統合ポリシー ---
// トークン使用量メトリクス + レート制限 + Content Safety + ヘッダー管理
resource aiGatewayApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = if (!empty(foundryEndpoint)) {
  parent: aiGatewayApi
  name: 'policy'
  properties: {
    format: 'xml'
    value: '''<policies>
  <inbound>
    <base />
    <!-- AI Gateway: トークンベースのレート制限（TPM） -->
    <azure-openai-token-limit
      tokens-per-minute="80000"
      counter-key="@(context.Subscription.Id)"
      estimate-prompt-tokens="true"
      remaining-tokens-header-name="x-ratelimit-remaining-tokens" />
    <!-- リクエスト追跡用ヘッダー -->
    <set-header name="X-Request-Id" exists-action="skip">
      <value>@(context.RequestId.ToString())</value>
    </set-header>
    <!-- Managed Identity でバックエンド認証 -->
    <authentication-managed-identity resource="https://cognitiveservices.azure.com" />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
    <!-- AI Gateway: トークン使用量メトリクスの発行 -->
    <azure-openai-emit-token-metric>
      <dimension name="Subscription ID" />
      <dimension name="API ID" />
    </azure-openai-emit-token-metric>
    <set-header name="X-Gateway" exists-action="override">
      <value>travel-marketing-ai-gateway</value>
    </set-header>
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>'''
  }
}

// --- AI Gateway: サブスクリプション（アクセスキー管理） ---
resource aiGatewaySubscription 'Microsoft.ApiManagement/service/subscriptions@2024-06-01-preview' = if (!empty(foundryEndpoint)) {
  parent: apim
  name: 'ai-gateway-subscription'
  properties: {
    displayName: 'AI Gateway Access'
    scope: '/apis/${aiGatewayApi.name}'
    state: 'active'
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
