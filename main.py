import configparser
import sys
from io import StringIO
from io import BytesIO

import requests
from urllib.parse import quote
from requests.auth import HTTPBasicAuth


config = configparser.ConfigParser(allow_no_value=True)
config.read("conf.ini")

JIRA_URL = config['jira']['url']
JIRA_USERNAME = config['jira']['username']
JIRA_PASSWORD = config['jira']['password']
JIRA_PROJECT = config['jira']['project']

GITLAB_URL = config['gitlab']['url']
GITLAB_TOKEN = config['gitlab']['token']
GITLAB_PROJECT = config['gitlab']['project']
GITLAB_PROJECT_ID = config['gitlab']['id']

# IMPORTANT !!!
# make sure that user (in gitlab) has access to the project you are trying to
# import into. Otherwise the API request will fail.

# if you want dates and times to be correct, make sure every user is (temporarily) admin

# jira user name as key, gitlab as value
GITLAB_USER_NAMES = {
    'ulgensrkvk': 'ulgens',
}

# Test Jira connection
print("Testing Jira connection...")
try:
    jira_connection = requests.get(
        JIRA_URL,
        auth=HTTPBasicAuth(JIRA_USERNAME, JIRA_PASSWORD),
        headers={'Content-Type': 'application/json'}
    )
except ConnectionError:
    raise ConnectionError(f"Couldn't connect to {JIRA_URL}")

if jira_connection.status_code != 200:
    print("Jira connection cannot be established!")
    print(f"{jira_connection.status_code}: {jira_connection.reason}")
    sys.exit(1)
else:
    print("Jira connection was successful!")

print("Fetching Jira issues...")
jira_issues_request = requests.get(
    JIRA_URL + 'rest/api/2/search?jql=project=%s+&maxResults=10000' % JIRA_PROJECT,
    auth=HTTPBasicAuth(JIRA_USERNAME, JIRA_PASSWORD),
    headers={'Content-Type': 'application/json'}
)

if jira_issues_request.status_code != 200:
    for message in jira_issues_request.json()["errorMessages"]:
        print(message)
    sys.exit(1)
else:
    jira_issues = jira_issues_request.json()["issues"]
    print(f"Found {len(jira_issues)} in project {JIRA_PROJECT}")


# Test Gitlab connection
gitlab_connection = requests.get(
    GITLAB_URL + 'api/v4/projects',
    headers={'PRIVATE-TOKEN': GITLAB_TOKEN}
)

if gitlab_connection.status_code != 200:
    print("Gitlab connection cannot be established!")
    print(f"{gitlab_connection.status_code}: {gitlab_connection.reason}")
    sys.exit(1)

# Find out the ID of the project
if not GITLAB_PROJECT_ID:
    gitlab_project_connection = requests.get(
        GITLAB_URL + f'api/v4/projects/{quote(GITLAB_PROJECT, safe="")}',
        headers={'PRIVATE-TOKEN': GITLAB_TOKEN}
    )

    if gitlab_project_connection.status_code != 200:
        print(f"{gitlab_project_connection.status_code}: {gitlab_project_connection.reason}")
        sys.exit(1)
    else:
        gitlab_project_id = gitlab_project_connection.json()["id"]
        gitlab_project_name = gitlab_project_connection.json()["path_with_namespace"]
        print(f"Project found: {gitlab_project_name} {gitlab_project_id }")

next_url = GITLAB_URL + f'api/v4/projects/{quote(GITLAB_PROJECT, safe="")}/issues?per_page=100'
not_finished = False
while (not_finished):
    res = requests.get(
        next_url,
        headers={'PRIVATE-TOKEN': GITLAB_TOKEN}
    )
    gitlab_issues = res.json()
    print(f"Try to delete {len(gitlab_issues)} old issues from gitlab.")
    for issue in gitlab_issues:
        res = requests.delete(
            GITLAB_URL + f'api/v4/projects/{quote(GITLAB_PROJECT, safe="")}/issues/{issue["iid"]}',
            headers={'PRIVATE-TOKEN': GITLAB_TOKEN}
        )
        if res.status_code != 204:
            print(f"Failed to delete old issue '{issue['iid']}' {res.status_code}-{res.reason}")

    links = res.links
    if 'next' in links:
        next_url = links['next']['url']
    else:
        not_finished = False

for issue in jira_issues:
    reporter = issue['fields']['reporter']['name']
    print(issue['key'] +' - '+ issue['fields']['summary'])

    assignee = []
    if 'assignee' in issue['fields'] and issue['fields']['assignee'] is not None and 'name' in issue['fields']['assignee']:
        if issue['fields']['assignee']['name'] in GITLAB_USER_NAMES:
            assignee.append(requests.get(
                GITLAB_URL + f'api/v4/users?username=' + GITLAB_USER_NAMES[issue['fields']['assignee']['name']],
                headers={'PRIVATE-TOKEN': GITLAB_TOKEN}
            ).json()[0]['id'])

    labels = ''
    labels += ',' + issue['fields']['status']['name']
    labels += ',' + issue['fields']['issuetype']['name']
    labels += ',' + issue['key']
    for fixVersion in issue['fields']['fixVersions']:
        labels += ',' + fixVersion['name']

    gl_issue = requests.post(
        GITLAB_URL + f'api/v4/projects/{quote(GITLAB_PROJECT, safe="")}/issues',
        headers={'PRIVATE-TOKEN': GITLAB_TOKEN},
        data={
            'title': issue['fields']['summary'],
            'description': issue['fields']['description'],
            'created_at': issue['fields']['created'],
            'assignee_ids': assignee,
            'labels': labels
        }
    ).json()['iid']

    if issue['fields']['resolutiondate'] is not None:
        res = requests.put(
            GITLAB_URL + f'api/v4/projects/{quote(GITLAB_PROJECT, safe="")}/issues/{gl_issue}',
            headers={'PRIVATE-TOKEN': GITLAB_TOKEN},
            data={
                'state_event': 'close',
                'closed_at': issue['fields']['resolutiondate']
            }
        )
        #print(res.json())

    # get comments and attachments
    issue_info = requests.get(
        JIRA_URL + 'rest/api/2/issue/%s/?fields=attachment,comment' % issue['id'],
        auth=HTTPBasicAuth(JIRA_USERNAME, JIRA_PASSWORD),
        headers={'Content-Type': 'application/json'}
    ).json()

    requests.post(
        GITLAB_URL + 'api/v4/projects/%s/issues/%s/notes' % (GITLAB_PROJECT_ID, gl_issue),
        headers={'PRIVATE-TOKEN': GITLAB_TOKEN},
        data={
            'body': JIRA_URL + 'browse/'+ issue['key']
        }
    )
    for comment in issue_info['fields']['comment']['comments']:
        author = comment['author']['name']

        note_add = requests.post(
            GITLAB_URL + 'api/v4/projects/%s/issues/%s/notes' % (GITLAB_PROJECT_ID, gl_issue),
            headers={'PRIVATE-TOKEN': GITLAB_TOKEN},
            data={
                'body': comment['body'],
                'created_at': comment['created']
            }
        )

    if len(issue_info['fields']['attachment']):
        for attachment in issue_info['fields']['attachment']:
            author = attachment['author']['name']

            _file = requests.get(
                attachment['content'],
                auth=HTTPBasicAuth(JIRA_USERNAME, JIRA_PASSWORD),
            )

            _content = BytesIO(_file.content)

            file_info = requests.post(
                GITLAB_URL + 'api/v4/projects/%s/uploads' % GITLAB_PROJECT_ID,
                headers={'PRIVATE-TOKEN': GITLAB_TOKEN},
                files={
                    'file': (
                        attachment['filename'],
                        _content
                    )
                },
            )

            del _content

            # now we got the upload URL. Let's post the comment with an
            # attachment
            requests.post(
                GITLAB_URL + 'api/v4/projects/%s/issues/%s/notes' % (GITLAB_PROJECT_ID, gl_issue),
                headers={'PRIVATE-TOKEN': GITLAB_TOKEN},
                data={
                    'body': file_info.json()['markdown'],
                    'created_at': attachment['created']
                }
            )
    print("created issue #%s" % gl_issue)

print("imported %s issues from project %s" % (len(jira_issues), JIRA_PROJECT))
