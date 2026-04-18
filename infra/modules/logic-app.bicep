// Azure Logic Apps (Consumption)
// post_approval_actions 用の HTTP workflow。
// 上司承認通知は別 workflow とし、ここでは Teams チャネル通知と SharePoint への成果物保存を扱う。

param name string
param location string
@description('承認後通知に使う Microsoft Teams connection resource 名。空なら Teams 通知は無効')
param teamsConnectionName string = ''
@description('承認後通知先の Team ID。空なら Teams 通知は無効')
param teamsTargetTeamId string = ''
@description('承認後通知先の Channel ID。空なら Teams 通知は無効')
param teamsTargetChannelId string = ''
@description('成果物保存先 SharePoint site ID。空なら SharePoint 保存は無効')
param sharePointSiteId string = ''
param tags object = {}

var teamsEnabled = !empty(teamsConnectionName) && !empty(teamsTargetTeamId) && !empty(teamsTargetChannelId)
var sharePointEnabled = !empty(sharePointSiteId)
var teamsConnectionId = teamsEnabled ? resourceId('Microsoft.Web/connections', teamsConnectionName) : ''
var teamsManagedApiId = teamsEnabled ? subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'teams') : ''

var requestSchema = {
  type: 'object'
  properties: {
    request_type: { type: 'string' }
    plan_title: { type: 'string' }
    plan_markdown: { type: 'string' }
    brochure_html: { type: 'string' }
    conversation_id: { type: 'string' }
  }
  required: [
    'request_type'
    'conversation_id'
  ]
}

var planFileNameExpression = '''@{concat('travel-marketing-ai-', formatDateTime(utcNow(),'yyyyMMdd-HHmmss'), '-', triggerBody()?['conversation_id'], '-plan.md')}'''
var brochureFileNameExpression = '''@{concat('travel-marketing-ai-', formatDateTime(utcNow(),'yyyyMMdd-HHmmss'), '-', triggerBody()?['conversation_id'], '-brochure.html')}'''
var planUploadUriTemplate = '''https://graph.microsoft.com/v1.0/sites/__SITE_ID__/drive/root:/@{outputs('Compose_plan_file_name')}:/content'''
var brochureUploadUriTemplate = '''https://graph.microsoft.com/v1.0/sites/__SITE_ID__/drive/root:/@{outputs('Compose_brochure_file_name')}:/content'''
var teamsChannelMessagePath = '/beta/teams/conversation/message/poster/User/location/Channel'
var teamsConnectionReferenceTemplate = '''@parameters('$connections')['__CONNECTION_NAME__']['connectionId']'''
var planUploadUri = replace(planUploadUriTemplate, '__SITE_ID__', sharePointSiteId)
var brochureUploadUri = replace(brochureUploadUriTemplate, '__SITE_ID__', sharePointSiteId)
var teamsConnectionReference = replace(teamsConnectionReferenceTemplate, '__CONNECTION_NAME__', teamsConnectionName)

var stubDefinition = {
  '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
  contentVersion: '1.0.0.0'
  triggers: {
    manual: {
      type: 'Request'
      kind: 'Http'
      inputs: {
        schema: requestSchema
      }
    }
  }
  actions: {
    Response: {
      type: 'Response'
      kind: 'Http'
      inputs: {
        statusCode: 202
        body: {
          status: 'accepted'
          message: 'Logic Apps workflow accepted the request'
        }
      }
    }
  }
}

var sharePointOnlyDefinition = {
  '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
  contentVersion: '1.0.0.0'
  triggers: {
    manual: {
      type: 'Request'
      kind: 'Http'
      inputs: {
        schema: requestSchema
      }
    }
  }
  actions: {
    Compose_plan_file_name: {
      type: 'Compose'
      inputs: planFileNameExpression
      runAfter: {}
    }
    Compose_brochure_file_name: {
      type: 'Compose'
      inputs: brochureFileNameExpression
      runAfter: {}
    }
    Upload_plan_markdown: {
      type: 'Http'
      runAfter: {
        Compose_plan_file_name: [
          'Succeeded'
        ]
      }
      inputs: {
        method: 'PUT'
        uri: planUploadUri
        headers: {
          'Content-Type': 'text/markdown; charset=utf-8'
        }
        body: '''@{triggerBody()?['plan_markdown']}'''
        authentication: {
          type: 'ManagedServiceIdentity'
          audience: 'https://graph.microsoft.com'
        }
      }
    }
    Upload_brochure_html: {
      type: 'Http'
      runAfter: {
        Compose_brochure_file_name: [
          'Succeeded'
        ]
        Upload_plan_markdown: [
          'Succeeded'
        ]
      }
      inputs: {
        method: 'PUT'
        uri: brochureUploadUri
        headers: {
          'Content-Type': 'text/html; charset=utf-8'
        }
        body: '''@{triggerBody()?['brochure_html']}'''
        authentication: {
          type: 'ManagedServiceIdentity'
          audience: 'https://graph.microsoft.com'
        }
      }
    }
    Response_succeeded: {
      type: 'Response'
      kind: 'Http'
      runAfter: {
        Upload_brochure_html: [
          'Succeeded'
        ]
      }
      inputs: {
        statusCode: 202
        body: {
          status: 'accepted'
          conversation_id: '''@{triggerBody()?['conversation_id']}'''
          plan_file_url: '''@{body('Upload_plan_markdown')?['webUrl']}'''
          brochure_file_url: '''@{body('Upload_brochure_html')?['webUrl']}'''
        }
      }
    }
    Response_failed_upload_plan: {
      type: 'Response'
      kind: 'Http'
      runAfter: {
        Upload_plan_markdown: [
          'Failed'
          'TimedOut'
        ]
      }
      inputs: {
        statusCode: 502
        body: {
          status: 'failed'
          stage: 'upload_plan_markdown'
          conversation_id: '''@{triggerBody()?['conversation_id']}'''
        }
      }
    }
    Response_failed_upload_brochure: {
      type: 'Response'
      kind: 'Http'
      runAfter: {
        Upload_brochure_html: [
          'Failed'
          'TimedOut'
        ]
      }
      inputs: {
        statusCode: 502
        body: {
          status: 'failed'
          stage: 'upload_brochure_html'
          conversation_id: '''@{triggerBody()?['conversation_id']}'''
        }
      }
    }
  }
}

var teamsOnlyDefinition = {
  '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
  contentVersion: '1.0.0.0'
  parameters: {
    '$connections': {
      type: 'Object'
      defaultValue: {}
    }
  }
  triggers: {
    manual: {
      type: 'Request'
      kind: 'Http'
      inputs: {
        schema: requestSchema
      }
    }
  }
  actions: {
    Compose_channel_message: {
      type: 'Compose'
      inputs: '''@{concat('<p>旅行プランの承認後処理が完了しました。<br />タイトル: ', triggerBody()?['plan_title'], '<br />Conversation ID: ', triggerBody()?['conversation_id'], '</p>')}'''
      runAfter: {}
    }
    Post_channel_message: {
      type: 'ApiConnection'
      runAfter: {
        Compose_channel_message: [
          'Succeeded'
        ]
      }
      inputs: {
        host: {
          connection: {
            name: teamsConnectionReference
          }
        }
        method: 'post'
        path: teamsChannelMessagePath
        body: {
          recipient: {
            groupId: teamsTargetTeamId
            channelId: teamsTargetChannelId
          }
          messageBody: '''@{outputs('Compose_channel_message')}'''
        }
      }
    }
    Response_succeeded: {
      type: 'Response'
      kind: 'Http'
      runAfter: {
        Post_channel_message: [
          'Succeeded'
        ]
      }
      inputs: {
        statusCode: 202
        body: {
          status: 'accepted'
          conversation_id: '''@{triggerBody()?['conversation_id']}'''
          target_team_id: teamsTargetTeamId
          target_channel_id: teamsTargetChannelId
        }
      }
    }
    Response_failed_post_message: {
      type: 'Response'
      kind: 'Http'
      runAfter: {
        Post_channel_message: [
          'Failed'
          'TimedOut'
        ]
      }
      inputs: {
        statusCode: 502
        body: {
          status: 'failed'
          stage: 'post_channel_message'
          conversation_id: '''@{triggerBody()?['conversation_id']}'''
        }
      }
    }
  }
}

var sharePointAndTeamsDefinition = {
  '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
  contentVersion: '1.0.0.0'
  parameters: {
    '$connections': {
      type: 'Object'
      defaultValue: {}
    }
  }
  triggers: {
    manual: {
      type: 'Request'
      kind: 'Http'
      inputs: {
        schema: requestSchema
      }
    }
  }
  actions: {
    Compose_plan_file_name: {
      type: 'Compose'
      inputs: planFileNameExpression
      runAfter: {}
    }
    Compose_brochure_file_name: {
      type: 'Compose'
      inputs: brochureFileNameExpression
      runAfter: {}
    }
    Upload_plan_markdown: {
      type: 'Http'
      runAfter: {
        Compose_plan_file_name: [
          'Succeeded'
        ]
      }
      inputs: {
        method: 'PUT'
        uri: planUploadUri
        headers: {
          'Content-Type': 'text/markdown; charset=utf-8'
        }
        body: '''@{triggerBody()?['plan_markdown']}'''
        authentication: {
          type: 'ManagedServiceIdentity'
          audience: 'https://graph.microsoft.com'
        }
      }
    }
    Upload_brochure_html: {
      type: 'Http'
      runAfter: {
        Compose_brochure_file_name: [
          'Succeeded'
        ]
        Upload_plan_markdown: [
          'Succeeded'
        ]
      }
      inputs: {
        method: 'PUT'
        uri: brochureUploadUri
        headers: {
          'Content-Type': 'text/html; charset=utf-8'
        }
        body: '''@{triggerBody()?['brochure_html']}'''
        authentication: {
          type: 'ManagedServiceIdentity'
          audience: 'https://graph.microsoft.com'
        }
      }
    }
    Compose_channel_message: {
      type: 'Compose'
      runAfter: {
        Upload_brochure_html: [
          'Succeeded'
        ]
      }
      inputs: '''@{concat('<p>旅行プランの承認後処理が完了しました。<br />タイトル: ', triggerBody()?['plan_title'], '<br />Conversation ID: ', triggerBody()?['conversation_id'], '<br />企画書: ', body('Upload_plan_markdown')?['webUrl'], '<br />ブローシャ: ', body('Upload_brochure_html')?['webUrl'], '</p>')}'''
    }
    Post_channel_message: {
      type: 'ApiConnection'
      runAfter: {
        Compose_channel_message: [
          'Succeeded'
        ]
      }
      inputs: {
        host: {
          connection: {
            name: teamsConnectionReference
          }
        }
        method: 'post'
        path: teamsChannelMessagePath
        body: {
          recipient: {
            groupId: teamsTargetTeamId
            channelId: teamsTargetChannelId
          }
          messageBody: '''@{outputs('Compose_channel_message')}'''
        }
      }
    }
    Response_succeeded: {
      type: 'Response'
      kind: 'Http'
      runAfter: {
        Post_channel_message: [
          'Succeeded'
        ]
      }
      inputs: {
        statusCode: 202
        body: {
          status: 'accepted'
          conversation_id: '''@{triggerBody()?['conversation_id']}'''
          plan_file_url: '''@{body('Upload_plan_markdown')?['webUrl']}'''
          brochure_file_url: '''@{body('Upload_brochure_html')?['webUrl']}'''
          target_team_id: teamsTargetTeamId
          target_channel_id: teamsTargetChannelId
        }
      }
    }
    Response_failed_upload_plan: {
      type: 'Response'
      kind: 'Http'
      runAfter: {
        Upload_plan_markdown: [
          'Failed'
          'TimedOut'
        ]
      }
      inputs: {
        statusCode: 502
        body: {
          status: 'failed'
          stage: 'upload_plan_markdown'
          conversation_id: '''@{triggerBody()?['conversation_id']}'''
        }
      }
    }
    Response_failed_upload_brochure: {
      type: 'Response'
      kind: 'Http'
      runAfter: {
        Upload_brochure_html: [
          'Failed'
          'TimedOut'
        ]
      }
      inputs: {
        statusCode: 502
        body: {
          status: 'failed'
          stage: 'upload_brochure_html'
          conversation_id: '''@{triggerBody()?['conversation_id']}'''
        }
      }
    }
    Response_failed_post_message: {
      type: 'Response'
      kind: 'Http'
      runAfter: {
        Post_channel_message: [
          'Failed'
          'TimedOut'
        ]
      }
      inputs: {
        statusCode: 502
        body: {
          status: 'failed'
          stage: 'post_channel_message'
          conversation_id: '''@{triggerBody()?['conversation_id']}'''
        }
      }
    }
  }
}

resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: sharePointEnabled && teamsEnabled
      ? sharePointAndTeamsDefinition
      : sharePointEnabled
          ? sharePointOnlyDefinition
          : teamsEnabled
              ? teamsOnlyDefinition
              : stubDefinition
    parameters: teamsEnabled ? {
      '$connections': {
        value: {
          '${teamsConnectionName}': {
            connectionId: teamsConnectionId
            connectionName: teamsConnectionName
            id: teamsManagedApiId
          }
        }
      }
    } : {}
  }
}

resource manualTrigger 'Microsoft.Logic/workflows/triggers@2019-05-01' existing = {
  parent: logicApp
  name: 'manual'
}

output id string = logicApp.id
output name string = logicApp.name
@secure()
output callbackUrl string = manualTrigger.listCallbackUrl().value
output principalId string = logicApp.identity.principalId
