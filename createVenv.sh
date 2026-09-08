#!/bin/bash
#
# Create venv for the plugin
# placed in the top level of the plugin path
# uses the manifest file to determine modules to be installed
pluginName="multiCam"
pluginVersion="plugin3.x.x"
baseDir="/home/stuart/DWC"

pluginDir="$baseDir/Plugins/$pluginName/$pluginVersion/Code"

# Venv needs to be in the plugin dir
# To satisfy pipINstall requiremets for the location of requirements.txt (if used)
venvDir=$pluginDir

manifestFile=$pluginDir/plugin.json

# Use pipInstall to create the venv and install dependencies
# Same method as used for plugins
pipInstall="/home/stuart/DWC/Plugins/pipInstall/Version2/pipInstall2.py"

# Make sure we are starting cleanly, remove venv if it exists

if [ -d "$venvDir/venv" ]
    then
    echo "Removing existing venv"
    source $venvDir/venv/bin/activate
    deactivate
    rm -rf $venvDir/venv
fi

#Create Venv if it does not exist
if [ ! -d "$venvDir/venv" ]
    then
		echo "Creating fresh venv in $venvDir"
        echo "Using Manifest $manifestFile"

        python $pipInstall -m $manifestFile -p  $venvDir

fi

echo "source $venvDir/venv/bin/activate"


