import copy
import flask
import json
import mwapi
import mwoauth
import mwoauth.flask
import os
import re
import yaml
from templates import templates
from translations import translations

app = flask.Flask(__name__)

__dir__ = os.path.dirname(__file__)
try:
    with open(os.path.join(__dir__, 'config.yaml')) as config_file:
        app.config.update(yaml.safe_load(config_file))
        flask_mwoauth = mwoauth.flask.MWOAuth(
            'https://www.wikidata.org',
            mwoauth.ConsumerToken(app.config['oauth']['consumer_key'], app.config['oauth']['consumer_secret'])
        )
        app.register_blueprint(flask_mwoauth.bp)
except FileNotFoundError:
    print('config.yaml file not found, assuming local development setup')

@app.template_filter('form2input')
def form2input(form):
    example = form['example']
    match = re.match(r'^(.*)\[(.*)\](.*)$', example)
    if match:
        (prefix, placeholder, suffix) = match.groups()
        return (flask.Markup.escape(prefix) +
                flask.Markup(r'<input type="text" name="form_representation" required placeholder="') +
                flask.Markup.escape(placeholder) +
                flask.Markup(r'"') +
                (flask.Markup(r' value="') + flask.Markup.escape(form['value']) + flask.Markup(r'"') if 'value' in form else flask.Markup('')) +
                flask.Markup(r'>') +
                flask.Markup.escape(suffix))
    else:
        raise Exception('Invalid template: missing [placeholder]: ' + example)

@app.route('/')
def index():
    return flask.render_template(
        'index.html',
        templates=templates,
    )

def process_template(template_name):
    if template_name not in templates:
        return flask.render_template(
            'no-such-template.html',
            template_name=template_name,
        )

    template = templates[template_name]

    if flask.request.method == 'POST':
        form_data = flask.request.form

        repeat_form = process_duplicates(template, form_data)
        if repeat_form:
            return repeat_form

        lexeme_data = build_lexeme(template, form_data)

        if 'oauth' in app.config:
            return submit_lexeme(template, lexeme_data)
        else:
            return flask.Response(lexeme_data, mimetype='application/json')
    else:
        return flask.render_template(
            'template.html',
            template=template,
            translations=translations[template['language_code']],
        )
if 'oauth' in app.config:
    process_template = mwoauth.flask.authorized(process_template)
process_template = app.route('/<template_name>/', methods=['GET', 'POST'])(process_template)

def process_duplicates(template, form_data):
    if 'no_duplicate' in form_data:
        return None

    duplicates = find_duplicates(template, form_data)
    if duplicates:
        return flask.render_template(
            'template.html',
            template=add_form_data_to_template(form_data, template),
            translations=translations[template['language_code']],
            duplicates=duplicates,
        )
    else:
        return None

def find_duplicates(template, form_data):
    if 'test' in template:
        session = mwapi.Session('https://test.wikidata.org')
    else:
        session = mwapi.Session('https://www.wikidata.org')
    lemma = form_data['form_representation']
    language = template['language_code']
    response = session.get(
        action='wbsearchentities',
        search=lemma,
        language=language,
        uselang=language, # for the result descriptions
        type='lexeme',
        limit=50,
    )
    matches = []
    for result in response['search']:
        if result['label'] == lemma:
            matches.append({'id': result['id'], 'uri': result['concepturi'], 'label': result['label'], 'description': result['description']})
    return matches

def add_form_data_to_template(form_data, template):
    template = copy.deepcopy(template)
    for (form_representation, form) in zip(form_data.getlist('form_representation'), template['forms']):
        form['value'] = form_representation
    return template

def build_lexeme(template, form_data):
    lang = template['language_code']
    return json.dumps({
        'type': 'lexeme',
        'lemmas': {lang: {'language': lang, 'value': form_data['form_representation']}},
        'language': template['language_item_id'],
        'lexicalCategory': template['lexical_category_item_id'],
        'claims': template['claims'],
        'forms': [
            {'add': '', 'representations': {lang: {'language': lang, 'value': form_representation}}, 'grammaticalFeatures': grammaticalFeatures, 'claims': []}
            for (form_representation, grammaticalFeatures) in zip(
                    form_data.getlist('form_representation'),
                    map(lambda form: form['grammatical_features_item_ids'], template['forms'])
            )
        ]
    })

def submit_lexeme(template, lexeme_data):
    if 'test' in template:
        host = 'test.wikidata.org'
    else:
        host = 'www.wikidata.org'
    session = flask_mwoauth.mwapi_session(
        host=host,
    )

    token = session.get(action='query', meta='tokens')['query']['tokens']['csrftoken']
    response = session.get(
        action='wbeditentity',
        new='lexeme',
        data=lexeme_data,
        token=token,
    )
    return flask.redirect('http://' + host + '/entity/' + response['entity']['id'], code=303)
