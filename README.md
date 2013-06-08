@author Michael Bommarito: [Bommarito Consulting, LLC](http://bommaritollc.com/)  
@date 20130211  

This is a sample automated deployment project for the SouthEast Michigan [Oracle Professionals Meetup (SEMOP) group](http://www.meetup.com/SouthEast-Michigan-Oracle-Professionals/).

The project includes a fabfile and necessary configuration to manage the deployment of an Oracle database instance:  
    * single instance; no RON/RAC  
    * no ASM  
    * no LVM/RAID configuration for ephemeral or EBS  
    * single host management; config does not support tracking multiple hosts  
    * very little template configuration  

This is all done with minimal exception handling and AWS or Oracle magic; sorry, not giving away the cow, just some milk.

If you're interested in a automated infrastructure or application deployment, Oracle or otherwise, please contact me at:  
    [michael@bommaritollc.com](mailto:michael@bommaritollc.com)  
    [http://bommaritollc.com/](http://bommaritollc.com/)
