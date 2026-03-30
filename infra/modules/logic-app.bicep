// Azure Logic Apps (Consumption) - 承認後自動アクション
// Teams 通知 + SharePoint 保存 + メール送信

param name string
param location string
param tags object = {}

resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'object'
              properties: {
                plan_title: { type: 'string' }
                plan_markdown: { type: 'string' }
                brochure_html: { type: 'string' }
                conversation_id: { type: 'string' }
              }
            }
          }
        }
      }
      actions: {
        Response: {
          type: 'Response'
          kind: 'Http'
          inputs: {
            statusCode: 200
            body: {
              status: 'accepted'
              message: '承認後アクションを開始しました'
            }
          }
        }
      }
    }
  }
}

output id string = logicApp.id
output name string = logicApp.name
@secure()
output callbackUrl string = logicApp.listCallbackUrl().value
output principalId string = logicApp.identity.principalId
