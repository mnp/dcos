import json
from contextlib import contextmanager

import pytest
import yaml
from docopt import DocoptExit

import ssh.tunnel
import test_util
from pkgpanda.util import load_json
from test_util.helpers import Host
from test_util.launch import get_launcher, LauncherError, main

MOCK_SSH_KEY_DATA = 'ssh_key_data'
MOCK_KEY_NAME = 'my_key_name'


def check_cli(cmd):
    assert main(cmd) == 0, 'Command failed! {}'.format(' '.join(cmd))


def check_success(capsys, tmpdir, config_str):
    """
    Runs through the required functions of a launcher and then
    runs through the default usage of the script for a
    given config path and info path, ensuring each step passes
    if all steps finished successfully, this parses and returns the generated
    info JSON and stdout description JSON for more specific checks
    """
    # Test launcher directly first
    config = yaml.safe_load(config_str)
    launcher = get_launcher(config['type'], config['provider_info'])
    info = launcher.create(config)
    launcher.wait(info)
    launcher.describe(info)
    launcher.test(info, 'py.test')
    launcher.delete(info)

    # add config to disk and make info path for CLI testing
    config_path = tmpdir.join('my_specific_config.yaml')  # test non-default name
    config_path.write(config_str)
    config_path = str(config_path)
    info_path = str(tmpdir.join('my_specific_info.json'))  # test non-default name

    # Now check launcher via CLI
    check_cli(['create', '--config-path={}'.format(config_path), '--info-path={}'.format(info_path)])
    # use the info written to disk to ensure JSON parsable
    info = load_json(info_path)
    # General assertions about info
    assert 'type' in info
    assert 'provider' in info
    assert 'ssh' in info
    assert 'user' in info['ssh']
    assert 'private_key' in info['ssh']

    check_cli(['wait', '--info-path={}'.format(info_path)])

    # clear stdout capture
    capsys.readouterr()
    check_cli(['describe', '--info-path={}'.format(info_path)])
    # capture stdout from describe and ensure JSON parse-able
    description = json.loads(capsys.readouterr()[0])

    # general assertions about description
    assert 'masters' in description
    assert 'private_agents' in description
    assert 'public_agents' in description

    check_cli(['pytest', '--info-path={}'.format(info_path)])

    check_cli(['delete', '--info-path={}'.format(info_path)])

    return info, description


@contextmanager
def mocked_context(*args, **kwargs):
    """ To be directly patched into an ssh.tunnel invocation to prevent
    any real SSH attempt
    """
    yield type('Tunnelled', (object,), {})


@pytest.fixture
def mocked_test_runner(monkeypatch):
    monkeypatch.setattr(ssh.tunnel, 'tunnel', mocked_context)
    monkeypatch.setattr(test_util.runner, 'integration_test', lambda *args, **kwargs: 0)


@pytest.fixture
def mocked_ssh_key_path(tmpdir):
    ssh_key_path = tmpdir.join('ssh_key')
    ssh_key_path.write(MOCK_SSH_KEY_DATA)
    return str(ssh_key_path)


@pytest.fixture
def mocked_aws_cf_simple_backend(monkeypatch, mocked_test_runner):
    """Does not include SSH key mocking
    """
    # mock create
    monkeypatch.setattr(test_util.aws.BotoWrapper, 'create_stack', lambda *args: None)
    # mock wait
    monkeypatch.setattr(test_util.aws.CfStack, 'get_stack_details', lambda _: {'StackStatus': 'CREATE_COMPLETE'})
    # mock describe
    monkeypatch.setattr(test_util.aws.DcosCfSimple, 'get_master_ips',
                        lambda _: [Host('127.0.0.1', '12.34.56')])
    monkeypatch.setattr(test_util.aws.DcosCfSimple, 'get_private_agent_ips',
                        lambda _: [Host('127.0.0.1', None)])
    monkeypatch.setattr(test_util.aws.DcosCfSimple, 'get_public_agent_ips',
                        lambda _: [Host('127.0.0.1', '12.34.56')])
    # mock delete
    monkeypatch.setattr(test_util.aws.DcosCfSimple, 'delete', lambda _: None)
    monkeypatch.setattr(test_util.aws.BotoWrapper, 'delete_key_pair', lambda *args: None)
    # mock config
    monkeypatch.setattr(test_util.aws.BotoWrapper, 'create_key_pair', lambda *args: MOCK_SSH_KEY_DATA)


@pytest.fixture
def mocked_aws_cf_simple(mocked_ssh_key_path, mocked_aws_cf_simple_backend):
    return """
---
this_is_a_temporary_config_format_do_not_put_in_production: yes_i_agree
type: cloudformation
template_url: http://us-west-2.amazonaws.com/downloads
stack_name: foobar
provider_info:
  region: us-west-2
  access_key_id: asdf09iasdf3m19238jowsfn
  secret_access_key: asdf0asafawwa3j8ajn
ssh_info:
  user: core
  private_key_path: {}
parameters:
  - ParameterKey: KeyName
    ParameterValue: default
  - ParameterKey: AdminLocation
    ParameterValue: 0.0.0.0/0
  - ParameterKey: PublicSlaveInstanceCount
    ParameterValue: 1
  - ParameterKey: SlaveInstanceCount
    ParameterValue: 5
""".format(mocked_ssh_key_path)


def test_aws_cf_simple(capsys, tmpdir, mocked_aws_cf_simple):
    """Test that required parameters are consumed and appropriate output is generated
    """
    info, desc = check_success(capsys, tmpdir, mocked_aws_cf_simple)
    # check AWS specific info
    assert info['type'] == 'cloudformation'
    assert 'stack_name' in info
    assert 'region' in info['provider']
    assert 'access_key_id' in info['provider']
    assert 'secret_access_key' in info['provider']
    assert info['ssh']['key_name'] == 'default'
    assert info['ssh']['delete_with_stack'] is False
    assert info['ssh']['private_key'] == MOCK_SSH_KEY_DATA
    assert info['ssh']['user'] == 'core'
    # check that description is updated with info
    assert 'stack_name' in desc


@pytest.mark.usefixtures('mocked_aws_cf_simple')
def test_aws_cf_simple_make_key(capsys, tmpdir):
    """ Same mocked backend as other aws_cf_simple tests, but marginally different config
    Test that required parameters are consumed and appropriate output is generated
    """
    config = """
---
this_is_a_temporary_config_format_do_not_put_in_production: yes_i_agree
type: cloudformation
template_url: http://us-west-2.amazonaws.com/downloads
stack_name: foobar
provider_info:
  region: us-west-2
  access_key_id: asdf09iasdf3m19238jowsfn
  secret_access_key: asdf0asafawwa3j8ajn
ssh_info:
  user: core
parameters:
  - ParameterKey: AdminLocation
    ParameterValue: 0.0.0.0/0
  - ParameterKey: PublicSlaveInstanceCount
    ParameterValue: 1
  - ParameterKey: SlaveInstanceCount
    ParameterValue: 5
"""
    info, desc = check_success(capsys, tmpdir, config)
    # check AWS specific info
    assert info['type'] == 'cloudformation'
    assert 'stack_name' in info
    assert 'region' in info['provider']
    assert 'access_key_id' in info['provider']
    assert 'secret_access_key' in info['provider']
    assert info['ssh']['key_name'] == 'foobar'
    assert info['ssh']['delete_with_stack'] is True
    assert info['ssh']['private_key'] == MOCK_SSH_KEY_DATA
    assert info['ssh']['user'] == 'core'
    # check that description is updated with info
    assert 'stack_name' in desc


def test_no_files_specified(tmpdir, mocked_aws_cf_simple):
    """Ensure typical usage works without specifying config and info file paths
    """
    with tmpdir.as_cwd():
        config_path = tmpdir.join('config.yaml')
        config_path.write(mocked_aws_cf_simple)
        assert main(['create']) == 0
        assert main(['wait']) == 0
        assert main(['describe']) == 0
        assert main(['pytest']) == 0
        assert main(['delete']) == 0


def test_noop():
    """Ensure docopt exit (displays usage)
    """
    with pytest.raises(DocoptExit):
        main([])
    with pytest.raises(DocoptExit):
        main(['foobar'])


def test_conflicting_path(tmpdir, mocked_aws_cf_simple):
    """Ensure default cluster info path is never overwritten
    by launching successive clusters
    """
    with tmpdir.as_cwd():
        tmpdir.join('config.yaml').write(mocked_aws_cf_simple)
        assert main(['create']) == 0
        assert main(['create']) == 1


def test_missing_input(tmpdir):
    """No files are provided so any operation should fail
    """
    with tmpdir.as_cwd():
        for cmd in ['create', 'wait', 'describe', 'delete', 'pytest']:
            with pytest.raises(FileNotFoundError):
                main([cmd])


def mock_stack_not_found(*args):
    raise Exception('Mock stack was not found!!!')


def test_missing_aws_stack(mocked_aws_cf_simple, monkeypatch):
    """ Tests that clean and appropriate errors will be raised
    """
    monkeypatch.setattr(test_util.aws.CfStack, '__init__', mock_stack_not_found)
    config = yaml.safe_load(mocked_aws_cf_simple)
    aws_launcher = get_launcher(config['type'], config['provider_info'])

    def check_stack_error(cmd, args):
        with pytest.raises(LauncherError) as exinfo:
            getattr(aws_launcher, cmd)(*args)
        assert exinfo.value.error == 'StackNotFound'

    info = aws_launcher.create(config)
    check_stack_error('wait', (info,))
    check_stack_error('describe', (info,))
    check_stack_error('delete', (info,))
    check_stack_error('test', (info, 'py.test'))


@pytest.mark.usefixtures('mocked_aws_cf_simple')
def test_aws_ssh_key_handling(mocked_ssh_key_path):
    provider_info = {
        'region': 'us-west-2',
        'access_key_id': 'foo',
        'secret_access_key': 'bar'}
    aws_launcher = get_launcher('cloudformation', provider_info)
    # Test most minimal config (nothing given and key is generated)
    ssh_info = aws_launcher.ssh_from_config({'stack_name': 'foo'})
    expected_ssh_info = {
        'delete_with_stack': True,
        'key_name': 'foo',
        'private_key': MOCK_SSH_KEY_DATA,
        'user': None}
    assert ssh_info == expected_ssh_info
    # Test KeyName in provided parameters and private key provided for testing
    ssh_info = aws_launcher.ssh_from_config({
        'stack_name': 'foo',
        'parameters': [{'ParameterKey': 'KeyName', 'ParameterValue': MOCK_KEY_NAME}],
        'ssh_info': {'private_key_path': mocked_ssh_key_path}})
    expected_ssh_info = {
        'delete_with_stack': False,
        'key_name': MOCK_KEY_NAME,
        'private_key': MOCK_SSH_KEY_DATA,
        'user': None}
    assert ssh_info == expected_ssh_info
    # Test key_name provided by ssh_info and not paramaters
    ssh_info = aws_launcher.ssh_from_config({
        'stack_name': 'foo',
        'ssh_info': {
            'key_name': MOCK_KEY_NAME,
            'private_key_path': mocked_ssh_key_path}})
    expected_ssh_info = {
        'delete_with_stack': False,
        'key_name': MOCK_KEY_NAME,
        'private_key': MOCK_SSH_KEY_DATA,
        'user': None}
    assert ssh_info == expected_ssh_info
    # Test private_key given, but no key_name to link against
    with pytest.raises(LauncherError):
        aws_launcher.ssh_from_config({
            'stack_name': 'foo',
            'ssh_info': {'private_key_path': mocked_ssh_key_path}})
    # Test redundant fields assigned
    with pytest.raises(LauncherError):
        aws_launcher.ssh_from_config({
            'stack_name': 'foo',
            'ssh_info': {'key_name': MOCK_KEY_NAME},
            'parameters': [{'ParameterKey': 'KeyName', 'ParameterValue': MOCK_KEY_NAME}]})
