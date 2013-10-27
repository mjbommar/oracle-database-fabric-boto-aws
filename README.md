@author Michael Bommarito: [Bommarito Consulting, LLC](http://bommaritollc.com/)  
@date 20131027

This is a sample automated Oracle database deployment project for:
    * the SouthEast Michigan [Oracle Professionals Meetup (SEMOP) group](http://www.meetup.com/SouthEast-Michigan-Oracle-Professionals/).
    * the 2013 [Michigan Oracle User Summit (MOUS)](http://mous.us/).

The project includes a fabfile and necessary configuration to manage the deployment of an Oracle database instance:  
    * single instance; no RON/RAC  
    * no ASM  
    * no LVM/RAID configuration for ephemeral or EBS  
    * single host management; config does not support tracking multiple hosts  
    * very little template configuration  

The original project, as developed in February 2013, supported only 11gR2 (11.2.0.3) installs.  As of October 2013, however, a '12c' branch is under development
for the installation and management of 12c (12.1.0.1) databases.

This is all done with minimal exception handling and AWS or Oracle magic; sorry, not giving away the cow, just some milk.

If you're interested in a automated infrastructure or application deployment, Oracle or otherwise, please contact me at:  
    [michael@bommaritollc.com](mailto:michael@bommaritollc.com)  
    [http://bommaritollc.com/](http://bommaritollc.com/)
