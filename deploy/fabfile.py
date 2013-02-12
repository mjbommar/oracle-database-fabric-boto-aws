'''
@author Michael Bommarito; http://bommaritollc.com/
@date 20130211

This fabfile manages the deployment of an Oracle database instance.
'''

# Standard imports
import csv
import datetime
import os.path
import sys
import time

# Fabric and fabtools imports
from fabric.api import run, sudo, settings, env, cd, put, execute
from fabric.colors import red, green, yellow
from fabric.contrib.files import exists, upload_template
from fabric.operations import reboot, prompt
from fabtools.system import get_sysctl, set_sysctl

# Boto imports
from boto.ec2.connection import EC2Connection
from boto.exception import EC2ResponseError
from boto.ec2.securitygroup import SecurityGroup

'''
Fabric configuration for executing host.
'''
USER_HOME = os.path.expanduser("~")

# Check env.key_filename.
if not env.key_filename:
    raise RuntimeError("No env.key_filename set; are you sure you passed -c fabric?")

if not os.path.exists(env.key_filename):
    if os.path.exists(os.path.join(USER_HOME, ".ssh", env.key_filename)):
        env.key_filename = os.path.join(USER_HOME, ".ssh", env.key_filename)
    else:
        raise RuntimeError("Unable to locate env.key_filename from fabric value {}.".format(env.key_filename))

# Determine base configuration directory; based on fabricrc path in env
CONFIG_DIR = os.path.dirname(os.path.abspath(env.rcfile))

# AWS Credentials
AWS_CREDENTIALS = list(csv.DictReader(open(os.path.join(CONFIG_DIR, "aws-credentials.txt"), "r"))).pop()
AWS_ACCESS_KEY = AWS_CREDENTIALS['Access Key Id']
AWS_SECRET_KEY = AWS_CREDENTIALS['Secret Access Key']

# Reboot wait time
REBOOT_TIME = 180

# AWS configuration
ami_id = "ami-3109d958"
instance_name = "oracle-database"
instance_type = "m2.2xlarge"
security_group_name = "oracle-database"

# Oracle configuration

'''
Fabric helper methods for command line usage.
'''
def test_ssh(user, host, timeout=5, retry_count=1, keyfile=env.key_filename):
    '''
    Test SSH connection from task host to target.
    '''
    with settings(warn_only=True):
        return run("ssh -t -i {} -o StrictHostKeyChecking=no -o ConnectTimeout={} -o ConnectionAttempts={} -o BatchMode=yes {}@{} uname -a".format(keyfile, timeout, retry_count, user, host))

'''
Fabric methods for deployment/configuration defined below.
'''

def yum_update():
    '''
    Refresh yum repository cache.
    '''
    # Update repo cache
    update_ret = sudo('yum -y makecache')
    if update_ret.failed:
        raise RuntimeError("Unable to update yum repository information.")

def yum(package_list=[], update_cache=True):
    '''
    Install required yum packages.
    '''
    # Update repo cache.
    if update_cache:
        yum_update()
    
    '''
    If no package list specified, assume from config;
    iterate over all lines of debian-requirements and `yum-get install`
    '''
    if len(package_list) == 0:
        csvFile = open(os.path.join(CONFIG_DIR, "yum-requirements.txt"))
        csv_reader = csv.reader(csvFile)
        for row in csv_reader:
            # Assign
            package = row[0]
            
            # Append if we're still here.
            package_list.append(package)
        
        # Close file
        csvFile.close()

    # Install the final package list        
    yum_ret = sudo("yum-get -y install {}".format(' '.join(package_list)))
    if yum_ret.failed:
        raise RuntimeError("Unable to install package {}.".format(package))

def yum_upgrade():
    '''
    Upgrade all installed yum packages.
    '''
    # Update repo cache
    yum_update()
    
    # Upgrade packages
    upgrade_ret = sudo('yum -y upgrade')
    if upgrade_ret.failed:
        raise RuntimeError("Unable to upgrade yum packages.")

def yum_upgrade_reboot():
    '''
    debian_upgrade() + reboot for first time/kernel installs.
    '''
    yum_upgrade()
    reboot(REBOOT_TIME)
        
def create_security_group():
    '''
    Create a single security group.
    '''
    # Check existing security groups for match.
    security_group_list = ec2_connection.get_all_security_groups()
    for security_group in security_group_list:
        if security_group.name == security_group_name:
            raise RuntimeError("Security group already exists.") 

    # First, create group.
    security_group = ec2_connection.create_security_group(security_group_name, "Security group for oracle-database")
    
    # Add ssh and OEM.
    security_group.authorize('tcp', 22, 22, '0.0.0.0/0')
    security_group.authorize('tcp', 1158, 1158, '0.0.0.0/0')
    

def launch_instance():
    '''
    Launch an Oracle database instance.
    '''
    # Assume the keypair name is based on our env.key_filename.
    instance_key_name = os.path.basename(env.key_filename).replace('.pem', '')
    
    reservation = ec2_connection.run_instances(ami_id, instance_type=instance_type, key_name=instance_key_name, security_groups=[security_group_name])
    instance = reservation.instances[0]
    print(green("Launching instance on reservation {}.".format(instance, reservation)))
    
    '''
    Wait for instance state to change;
    if it doesn't change to running, then fail.
    '''    
    print(yellow('Waiting for instance to start...'))
    set_tags = False
    while instance.state == u'pending':
        # Try to set tags.
        if set_tags == False:
            try:
                ec2_connection.create_tags([instance.id], {"Name": instance_name})
                set_tags = True
                print(green("Instance {} tagged.".format(instance)))
            except EC2ResponseError, e:
                print(red("Tagging failed; sleeping, updating instance, and trying again."))
        
        # Check up on its status every so often
        time.sleep(10)
        instance.update()

    # Fail if we aren't running.
    if instance.state != u'running':
        raise RuntimeError("Instance {} state is {}.".format(instance, instance.state))

    # Otherwise, print our status.    
    print(green("Instance state: {}".format(instance.state)))
    print(green("Public DNS: {}".format(instance.public_dns_name)))

    '''
    Wait for the instance to be available over SSH; 
    if it fails, we fail the task.
    '''
    
    # Flush SSH known_hosts key if we are using EIPs
    with settings(warn_only=True):
        run("ssh-keygen -R {}".format(instance.public_dns_name))
    
    print(yellow('Waiting for SSH to come up...'))
    ssh_ret = test_ssh('root', instance.public_dns_name, timeout=15, retry_count=12)
    if ssh_ret.succeeded:
        print(green("Successfully connected on SSH."))
    else:
        raise RuntimeError("Unable to connect over SSH to host {} with timeout/retry_count settings.")

# Create EC2 connection
ec2_connection = EC2Connection(aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)
