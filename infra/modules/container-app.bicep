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

// ACR イメージを使う場合のみ registry 参照が必要
var isAcrImage = contains(imageName, '.azurecr.io')
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
], !empty(projectEndpoint) ? [
  {
    name: 'AZURE_AI_PROJECT_ENDPOINT'
    value: projectEndpoint
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
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: isAcrImage ? [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'travel-agents'
          image: imageName
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: containerEnv
        }
      ]
      scale: {
        minReplicas: 0
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
