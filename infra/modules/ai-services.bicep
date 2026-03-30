// Microsoft Foundry リソース（CognitiveServices/accounts + allowProjectManagement）

param name string
param location string
param tags object = {}
param modelDeploymentName string = 'gpt-5-4-mini'
param imageModelDeploymentName string = 'gpt-image-1.5'

resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: toLower(name)
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

// gpt-5.4-mini モデルデプロイメント
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: aiFoundry
  name: modelDeploymentName
  sku: {
    capacity: 100
    name: 'GlobalStandard'
  }
  properties: {
    model: {
      name: 'gpt-5.4-mini'
      format: 'OpenAI'
      version: '2026-03-17'
    }
  }
}

// GPT Image 1.5 モデルデプロイメント
resource imageModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: aiFoundry
  name: imageModelDeploymentName
  sku: {
    capacity: 9
    name: 'GlobalStandard'
  }
  properties: {
    model: {
      name: 'gpt-image-1.5'
      format: 'OpenAI'
      version: '2025-12-16'
    }
  }
}

output id string = aiFoundry.id
output name string = aiFoundry.name
output endpoint string = aiFoundry.properties.endpoint
output principalId string = aiFoundry.identity.principalId
