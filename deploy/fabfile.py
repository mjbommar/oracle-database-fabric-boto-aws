'''
@author Michael Bommarito; http://bommaritollc.com/
@date 20130211

This fabfile manages the deployment of an Oracle database instance:
    * single instance; no RON/RAC
    * no ASM
    * no LVM/RAID configuration for ephemeral or EBS
    * single host management; config does not support tracking multiple hosts
    * very little template configuration 

This is all done with minimal exception handling and AWS magic; sorry, not
giving away the cow, just some milk.

If you're interested in a deployment like this, please contact me at:
    michael@bommaritollc.com
    http://bommaritollc.com/

Sample runs:
    Start @ 3:04, End @ 
    
     
    
    
'''

# Standard imports
import csv
import datetime
import os.path
import re
import sys
import time

# Fabric and fabtools imports
from fabric.api import run, sudo, settings, env, cd, put, execute
from fabric.colors import red, green, yellow
from fabric.contrib.files import exists, upload_template
from fabric.operations import reboot, prompt, get
from fabtools.system import get_sysctl, set_sysctl

# Boto imports
from boto.ec2.connection import EC2Connection
from boto.exception import EC2ResponseError
from boto.ec2.blockdevicemapping import EBSBlockDeviceType, BlockDeviceMapping
from StringIO import StringIO

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

# Determine configuration and template directories based on -c argument to fabric.
CONFIG_DIR = os.path.dirname(os.path.abspath(env.rcfile))
TEMPLATE_DIR = os.path.abspath(os.path.join(CONFIG_DIR, "..", "template/"))

# AWS Credentials
AWS_CREDENTIALS = list(csv.DictReader(open(os.path.join(CONFIG_DIR, "aws-credentials.txt"), "r"))).pop()
AWS_ACCESS_KEY = AWS_CREDENTIALS['Access Key Id']
AWS_SECRET_KEY = AWS_CREDENTIALS['Secret Access Key']

# Reboot wait time
REBOOT_TIME = 180
LAUNCH_TIME = 300

# AWS configuration
ami_id = "ami-8f4083e6"
instance_name = "oracle-database"
instance_type = "m1.xlarge"
security_group_name = "oracle-database"

# Oracle configuration
oracle_installer_uri = "https://s3.amazonaws.com/bommarito-consulting/app/oracle/oracle-database-11.2.0.3.tar.gz"
oracle_tmp_pattern = re.compile('/tmp/([a-zA-Z0-9_\-]+)\.', re.IGNORECASE)
oracle_log_pattern = re.compile('/u01/app/oraInventory/logs/.+')

'''
Fabric helper methods for command line usage.
'''
def test_ssh(user, host, timeout=5, retry_count=1, keyfile=env.key_filename):
    '''
    Test SSH connection from task host to target.
    '''
    return run_quiet("ssh -t -i {} -o StrictHostKeyChecking=no -o ConnectTimeout={} -o ConnectionAttempts={} -o BatchMode=yes {}@{} uname -a".format(keyfile, timeout, retry_count, user, host))

def run_quiet(command, use_sudo=False):
    '''
    Run a command quietly.
    '''
    with settings(warn_only=True):
        if use_sudo:
            return sudo(command)
        else:
            return run(command)
        
def get_host():
    '''
    Get the currently set host from hosts.txt
    '''
    with open(os.path.join(CONFIG_DIR, 'hosts.txt'), 'r') as host_file:
        return host_file.read().strip()

def set_host(host_string):
    '''
    Set the current host from hosts.txt
    '''
    with open(os.path.join(CONFIG_DIR, 'hosts.txt'), 'w') as host_file:
        host_file.write(host_string)
        
def update_host():
    '''
    Update the fabric host environment variable based on hosts.txt
    '''
    env.host_string = get_host()   

'''
Fabric methods for deployment/configuration defined below.
'''

def yum_update():
    '''
    Refresh yum repository cache.
    '''
    # Update repo cache
    update_ret = run_quiet('yum -y makecache', use_sudo=True)
    if update_ret.failed:
        raise RuntimeError("Unable to update yum repository information.")

def yum_install(package_list=[], update_cache=False):
    '''
    Install required yum packages.
    '''
    # Update repo cache.
    if update_cache:
        yum_update()
    
    '''
    If no package list specified, assume from config;
    iterate over all lines of yum-requirements and `yum install`
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
    yum_ret = run_quiet("yum -y install {}".format(' '.join(package_list)), use_sudo=True)
    if yum_ret.failed:
        raise RuntimeError("Unable to install package {}.".format(package))

def yum_upgrade(update_cache=False):
    '''
    Upgrade all installed yum packages.
    '''
    # Update repo cache
    if update_cache:
        yum_update()
    
    # Complete pending transactions if not clean.
    pending_ret = run_quiet('yum-complete-transaction -y', use_sudo=True)
    pending_ret = run_quiet('package-cleanup --problems', use_sudo=True)
    
    # Upgrade packages
    upgrade_ret = run_quiet('yum -y --skip-broken upgrade', use_sudo=True)
    if upgrade_ret.failed:
        raise RuntimeError("Unable to upgrade yum packages.")

def yum_upgrade_reboot():
    '''
    yum_upgrade() + reboot for first time/kernel installs.
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


def enroll_oel():
    '''
    Enroll an instance in the OEL repo by uploading the yum repo config.
    '''
    upload_template("public-yum-el5.repo_template", "/etc/yum.repos.d/public-yum-el5.repo", use_sudo=True, template_dir=TEMPLATE_DIR, use_jinja=True)
    yum_update()

def resize_root():
    '''
    We have to do this on older OS configs.
    '''
    run_quiet('resize2fs /dev/sda1')

def setup_oracle_user():
    '''
    Setup the Oracle user keys.
    '''
    # Setup oracle SSH keys.
    run_quiet('mkdir -p /home/oracle/.ssh/', use_sudo=True)
    run_quiet('cp /root/.ssh/authorized_keys /home/oracle/.ssh/', use_sudo=True)
    run_quiet('chown -R oracle:oinstall /home/oracle/.ssh/', use_sudo=True)
    run_quiet('chmod -R 700 /home/oracle/.ssh/')
    run_quiet('chmod 600 /home/oracle/.ssh/authorized_keys', use_sudo=True)
    
    # Setup bash_profile
    run_quiet("echo ORACLE_HOME=/u01/app/oracle/product/11.2.0/dbhome_1 >> /home/oracle/.bash_profile")
    run_quiet("echo ORACLE_BASE=/u01/app/oracle >> /home/oracle/.bash_profile")
    run_quiet("echo ORACLE_SID=oracle >> /home/oracle/.bash_profile")
    run_quiet("echo ORACLE_UNQNAME=oracle >> /home/oracle/.bash_profile")
    run_quiet("echo 'PATH=$PATH:$ORACLE_HOME/bin/' >> /home/oracle/.bash_profile")
    run_quiet("echo export ORACLE_HOME ORACLE_BASE ORACLE_SID ORACLE_UNQNAME PATH >> /home/oracle/.bash_profile")

def disable_software_firewall():
    '''
    Disable iptables software firewall; sometimes useful for QoS/routing,
    but easier in this example and basic whitelisting done at hardware level.
    '''
    run_quiet('chkconfig iptables off', use_sudo=True)
    run_quiet('service iptables stop', use_sudo=True)

def setup_db_reqs():
    '''
    Setup (some of) the Oracle Optimal Flexible Architecture (OFA) and Oracle
    database requirements.
    
    Most are handled by oracle-validated package.
    '''
    # Get our keys into oracle user.
    setup_oracle_user()
    
    # Setup OFA paths and download installer.
    run_quiet('mkdir -p /u01/install', use_sudo=True)
    run_quiet('groupadd oper', use_sudo=True)
    
    # Download Oracle installer.
    with cd('/u01/install/'):
        tar_file_name = os.path.basename(oracle_installer_uri)
        if not exists(tar_file_name):
            run_quiet('wget {}'.format(oracle_installer_uri))
        
        run_quiet('tar xzf {}'.format(tar_file_name))
    
    # chown/chmod paths.    
    run_quiet('chown -R oracle:oinstall /u01', use_sudo=True)
    run_quiet('chmod -R 775 /u01', use_sudo=True)   
    
def install_db():
    '''
    Upload the response file template and run the Oracle installer.
    '''
   
    '''
    Upload response file to Oracle home.
    We need to get some contextual information from the host.
    '''
    hostname = run('hostname')
    TEMPLATE_CONTEXT = {'ORACLE_HOSTNAME': hostname}
    upload_template("database.rsp_template", "/home/oracle/database.rsp", 
                    template_dir=TEMPLATE_DIR, use_jinja=True, 
                    context=TEMPLATE_CONTEXT)
    
    # OK, run the installer from response file now.
    with cd('/u01/install/database/'):
        # Clear old nohup and launch.
        run_quiet('rm -f ./nohup.out')
        output = run_quiet('nohup ./runInstaller -responseFile /home/oracle/database.rsp -ignoreSysPrereqs -ignorePrereq -silent')
        
        # Wait until log is available in oraInventory; sloppy hack timing.
        oracle_log_buffer = None
        oracle_log = None
        
        for i in range(6):
            # Update buffer
            get('nohup.out', local_path="./nohup.out")
            oracle_log_buffer = open('nohup.out', 'r').read()
            oracle_log_match = oracle_log_pattern.findall(oracle_log_buffer)
            if len(oracle_log_match) > 0:
                oracle_log = oracle_log_match.pop()
                break
            else:
                time.sleep(5)
        
        if not oracle_log:
            print(red("Unable to detect log match in nohup."))
        
        # Tail the log until we see that the install is complete.
        oracle_log_buffer = None
        
        while True:
            # Update local copy.
            # Update buffer
            get(oracle_log, local_path="./oracle_log.txt")
            oracle_log_buffer = open('./oracle_log.txt', 'r').read()
            
            if 'INFO: Unloading Setup Driver' in oracle_log_buffer:
                print(green("Installation complete."))
                break
            else:
                print(yellow("Tailing Oracle log file:"))
                run_quiet('tail {}'.format(oracle_log))
                
            # OK, wait a few.
            time.sleep(10)
                
def install_db_post():
    '''
    Run post-installation steps as root.
    '''
    # Execute root scripts
    run_quiet('/u01/app/oraInventory/orainstRoot.sh', use_sudo=True)
    run_quiet('/u01/app/oracle/product/11.2.0/dbhome_1/root.sh', use_sudo=True)

def create_listener():
    '''
    Run netca with a response file to create a listener.
    '''
    # Upload netca template
    upload_template("netca.rsp_template", "/home/oracle/netca.rsp", 
                    template_dir=TEMPLATE_DIR, use_jinja=True)
    
    # Run netca
    run_quiet('netca -silent -responseFile /home/oracle/netca.rsp')

def create_database():
    '''
    Run dbca with a response file to create a database.
    '''
    # Upload netca template
    upload_template("dbca.rsp_template", "/home/oracle/dbca.rsp", 
                    template_dir=TEMPLATE_DIR, use_jinja=True)
    
    # Run netca
    run_quiet('dbca -silent -responseFile /home/oracle/dbca.rsp')
    
def post_launch(host_string, host_string_oracle, skip_updates=False):
    '''
    Run post-launch steps.
    '''
    # Resize root EBS volume for older RHEL instance.
    execute(resize_root, hosts=[host_string])

    # Disable software firewall
    execute(disable_software_firewall, hosts=[host_string])
    
    # Update and reboot host.
    if not skip_updates:
        execute(yum_upgrade_reboot, hosts=[host_string])
    
    # Enroll in OEL yum repo
    execute(enroll_oel, hosts=[host_string])
    execute(yum_install, hosts=[host_string])
    
    # Setup DB/OFA requirements, e.g., oracle user, /u01/
    execute(setup_db_reqs, hosts=[host_string])
    
    # Now install the DB software and run post-installation.
    execute(install_db, hosts=[host_string_oracle])
    execute(install_db_post, hosts=[host_string])
    
    # Now create a listener and database with dbca/netca.
    execute(create_listener, hosts=[host_string_oracle])
    execute(create_database, hosts=[host_string_oracle])

def launch_instance(skip_updates=False):
    '''
    Launch an Oracle database instance.
    '''
    # Assume the keypair name is based on our env.key_filename.
    instance_key_name = os.path.basename(env.key_filename).replace('.pem', '')
    
    # Check that we have a security group configured already.
    security_group_list = ec2_connection.get_all_security_groups()
    security_group_found = False
    for security_group in security_group_list:
        if security_group.name == security_group_name:
            security_group_found = True
            break
    
    # If we didn't find it, create it.
    if not security_group_found:
        create_security_group()    
    
    # We want a larger EBS root volume, so override /dev/sda1.
    # Create an EBS device with 40GB allocated.
    dev_root = EBSBlockDeviceType()
    dev_root.size = 40
    
    # Create the mapping.
    dev_mapping = BlockDeviceMapping()
    dev_mapping['/dev/sda1'] = dev_root 
    
    reservation = ec2_connection.run_instances(ami_id, 
                       instance_type=instance_type, key_name=instance_key_name, 
                       security_groups=[security_group_name], 
                       block_device_map = dev_mapping)
    
    # This is hacky but (mostly) works.
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
        run_quiet("ssh-keygen -R {}".format(instance.public_dns_name))
    
    print(yellow('Waiting for SSH to come up...'))
    print(yellow('Waiting for {} seconds...'.format(LAUNCH_TIME)))
    time.sleep(LAUNCH_TIME)
    
    # Try to connect.
    ssh_ret = test_ssh('root', instance.public_dns_name, timeout=30, retry_count=20)
    if ssh_ret.succeeded:
        print(green("Successfully connected on SSH."))
    else:
        raise RuntimeError("Unable to connect over SSH to host {} with timeout/retry_count settings.")
    
    # Set host string
    host_string = "{}@{}".format("root", instance.public_dns_name)
    host_string_oracle = "{}@{}".format("oracle", instance.public_dns_name)
    set_host(host_string)

    # Run post-launch steps.
    post_launch(host_string, host_string_oracle, skip_updates=skip_updates)

# Create EC2 connection
ec2_connection = EC2Connection(aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)

# If no -H argument was specified, update host from config file.
if not env.host_string:
    update_host()
