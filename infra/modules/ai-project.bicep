// Foundry Project（CognitiveServices/accounts/projects）

param name string
param location string
param tags object = {}
param aiFoundryName string

resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: aiFoundryName
}

resource aiProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  name: name
  parent: aiFoundry
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

output id string = aiProject.id
output name string = aiProject.name
output principalId string = aiProject.identity.principalId
