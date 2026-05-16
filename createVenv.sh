VENV_DIR="./venv"
PLUGIN_VERSION="plugin3.6.x"
# make sure we are at the system python level
if [ -d "$VENV_DIR" ]
    then
    echo 'Deactivating venv'
    source ./venv/bin/activate
    deactivate
    echo 'Removing venv'
    rm -rf $VENV_DIR
fi
#Create Venv if it does not exist
if [ ! -d "$VENV_DIR" ]
    then
		echo 'Creating new venv'
        python -m venv $VENV_DIR --clear --system-site-packages --upgrade-deps

fi
source ./venv/bin/activate
echo 'Installing pip modules'
python -m pip install -r ./$PLUGIN_VERSION/Code/dsf/requirements.txt
