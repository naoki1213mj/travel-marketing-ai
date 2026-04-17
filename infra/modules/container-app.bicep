// Container App (FastAPI + React)

param name string
param location string
param tags object = {}
param containerAppsEnvironmentId string
param containerRegistryName string
param imageName string
param keyVaultName string
param appInsightsConnectionString string
param modelName string = 'gpt-5-4-mini'
param projectEndpoint string = ''
param imageProjectEndpointMai string = ''
param cosmosDbEndpoint string = ''

param contentUnderstandingEndpoint string = ''
param speechServiceEndpoint string = ''
param speechServiceRegion string = ''
param voiceSpaClientId string = ''
param tenantId string = ''
param improvementMcpEndpoint string = ''
@secure()
param logicAppCallbackUrl string = ''
@secure()
param managerApprovalTriggerUrl string = ''

var containerSecrets = concat(!empty(logicAppCallbackUrl) ? [
  {
    name: 'logic-app-callback-url'
    value: logicAppCallbackUrl
  }
] : [], !empty(managerApprovalTriggerUrl) ? [
  {
    name: 'manager-approval-trigger-url'
    value: managerApprovalTriggerUrl
  }
] : [])

// 初回 provision は公開イメージを使う場合があるが、後段の azd deploy では ACR イメージへ切り替わる。
// 先に registry 設定を入れておかないと、deploy 時に pull 認証が不足して revision 作成が失敗する。
var containerEnv = concat([
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsightsConnectionString
  }
  {
    name: 'SERVE_STATIC'
    value: 'true'
  }
  {
    name: 'MODEL_NAME'
    value: modelName
  }
  {
    name: 'ENVIRONMENT'
    value: 'production'
  }
], !empty(projectEndpoint) ? [
  {
    name: 'AZURE_AI_PROJECT_ENDPOINT'
    value: projectEndpoint
  }
] : [], !empty(imageProjectEndpointMai) ? [
  {
    name: 'IMAGE_PROJECT_ENDPOINT_MAI'
    value: imageProjectEndpointMai
  }
] : [], !empty(cosmosDbEndpoint) ? [
  {
    name: 'COSMOS_DB_ENDPOINT'
    value: cosmosDbEndpoint
  }
] : [], !empty(contentUnderstandingEndpoint)? [
  {
    name: 'CONTENT_UNDERSTANDING_ENDPOINT'
    value: contentUnderstandingEndpoint
  }
] : [], !empty(speechServiceEndpoint) ? [
  {
    name: 'SPEECH_SERVICE_ENDPOINT'
    value: speechServiceEndpoint
  }
] : [], !empty(speechServiceRegion) ? [
  {
    name: 'SPEECH_SERVICE_REGION'
    value: speechServiceRegion
  }
] : [], !empty(logicAppCallbackUrl) ? [
  {
    name: 'LOGIC_APP_CALLBACK_URL'
    secretRef: 'logic-app-callback-url'
  }
] : [], !empty(managerApprovalTriggerUrl) ? [
  {
    name: 'MANAGER_APPROVAL_TRIGGER_URL'
    secretRef: 'manager-approval-trigger-url'
  }
] : [], !empty(voiceSpaClientId) ? [
  {
    name: 'VOICE_SPA_CLIENT_ID'
    value: voiceSpaClientId
  }
] : [], !empty(tenantId) ? [
  {
    name: 'AZURE_TENANT_ID'
    value: tenantId
  }
] : [], !empty(improvementMcpEndpoint) ? [
  {
    name: 'IMPROVEMENT_MCP_ENDPOINT'
    value: improvementMcpEndpoint
  }
] : [])

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: containerRegistryName
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: union(tags, {
    'azd-service-name': 'web'
  })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      secrets: containerSecrets
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'travel-agents'
          image: imageName
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: containerEnv
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health'
                port: 8000
              }
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/api/ready'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

// Key Vault Secrets User ロール割り当て
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, containerApp.id, '4633458b-17de-408a-b874-0445c86b69e6')
  scope: keyVault
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') // Key Vault Secrets User
  }
}

// ACR Pull ロール割り当て
resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, containerApp.id, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  scope: acr
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
  }
}

output id string = containerApp.id
output name string = containerApp.name
output uri string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output principalId string = containerApp.identity.principalId
