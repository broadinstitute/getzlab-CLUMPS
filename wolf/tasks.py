import wolf
#import pandas as pd
#pd.set_option('display.max_colwidth', None)
#pd.set_option('display.max_rows', None)
import re
import subprocess

#tasks.py
#v55 most recent
#speed_v63
CLUMPS_DOCKER_IMAGE = "gcr.io/broad-getzlab-workflows/clumps_emprint_ak_old:v2"

class clumps_prep_task(wolf.Task):
    # Preparation for clumps input files:
    # computes mutational frequencies, spectra, and identifies protein structures needed
    resources = { "mem" : "8G" }
    
    # input data for the 'prep' step is the mutation annotation file (maf)
    # <Required> Input file for CLUMPS. Default expects .maf
    inputs = {
        "inMaf" : None,
        "genome_2bit" : None,
        "fasta" : None, 
        "scatterWidth" : None,
        "huniprot2pdb" : None
    }

    script = """
    #mkdir clumps_preprocess
    #clumps-prep --input ${maf} --output_dir clumps_preprocess --hgfile ${genome_2bit} --fasta ${fasta} --gpmaps ${gpmaps}
    ln ${fasta} .
    ln ${genome_2bit} .
    
    #Need to capitalize start position -.-
    sed 's/Start_position/Start_Position/' ${inMaf} > tmp.maf
    


    python /sw/src/GPmapper.py tmp.maf #${inMaf}
    #tar cfz mutfilesSplitByProtein.tar.gz splitByProtein/

    ## CALCULATE SAMPLE MUTATIONAL FREQUENCIES AND MUTATIONAL SPECTRA
    python /sw/src/calcSampleMutationFrequencies.py tmp.maf #${inMaf}
    python /sw/src/calcMutationContexts.py tmp.maf #${inMaf}

    #huniprot2pdb_ungz=`echo ${huniprot2pdb} | sed -r "s/\.gz$//g"`
    #zcat ${huniprot2pdb} > $huniprot2pdb_ungz
    #ls -alh $huniprot2pdb_ungz
    split -d --number=l/${scatterWidth} -a 5 $huniprot2pdb huniprot2pdb_chunk_
    find huniprot2pdb_chunk_* -exec gzip {} \;
    
    """

    output_patterns = {
        "mutations" : "splitByProtein",
        "sampleMutFreq" : "sampleMutFreq.txt",
        "sampleMutSpectra" : "sampleMutSpectra.txt",
        "prot2pdbchunks" : "huniprot2pdb_chunk_*"
    }
    
    docker = CLUMPS_DOCKER_IMAGE

class clumps_run_task(wolf.Task):
    # this task is the main clumps processing/algorithm
    #resources = { "partition" : "n1-highcpu-64-nonp", "cpus-per-task" : 64, "mem": "50200M" }
    resources = {"cpus-per-task" : 16}
    conf = {"clust_frac": 1}
    # the input files for this step are the different individual prot2pdb chunks from the huniprot2pdb_chunks folder
    # provide a list of all the individual prot2pdb chunks (or the file path to each prot2pdb chunks file)

    # <Required> Directory of files titled with Uniprot IDs that have mutation information
    # <Required> File mapping uniprot ID to PDB ID with residue-level mapping information.
    # coverage_track is on the gs bucket
    inputs = {
        #"clumps_preprocess" : None,
        "mutationsTarball" :None,
        "sampleMutFreq" : "sampleMutFreq.txt",
        "sampleMutSpectra" : "sampleMutSpectra.txt",
        "setfile" : None,  #specifies #permutations, protein file location, hillexp, pancanfactor, and other things
        "prot2pdb_chunks" : None,
        "pdb_dir" : None,
        "coverage_track" : None,
        "coverage_track_index" : None, # not actually used as an input; just needs to be localized alongside coverage_track
        "genome_2bit" : None,
        "fasta" : None,
        #"gpmaps" : None, #unsure if this gets used
        "sampler" : "UniformSampler",
        "nthreads" : 16,
        "timeout" : 0,
        "ttype" : "pancan" ,
        #"pancan_factor" : 1,
        #"hillexp" : 4
        "lineId" : -1
    }

    overrides = { "prot2pdb_chunks" : "delayed" }

    
    script = """
    ## making sure we're writing to local disk (not boot disk)
    mkdir sw
    cp -vr /sw/* sw
    rm -rf /sw
    ln -vs $PWD/sw /sw
    #unpack mutations from clumps prep
    #tar xzvf ${mutationsTarball} && mv splitByProtein /sw/dat/
    cp -r ${mutationsTarball} /sw/dat/

    #link 2bit, fasta locally a
    ln ${genome_2bit} /sw/src/
    ln ${fasta}  /sw/src/
    
    mkdir -p /sw/dat/ftp.wwpdb.org/pub/pdb/data/structures/divided/
    ln ${pdb_dir} /sw/dat/ftp.wwpdb.org/pub/pdb/data/structures/divided/pdb #... think I need to do this so pdb is in expected location? 
    
    #then this is done to parse through huniprot list
    if [ ${lineId} = -1 ]; then
        nlines=`zcat ${prot2pdb_chunks} | wc -l`
        lines=$(seq 1 $nlines)
    else
        lines=${lineId}
    fi
    
    START_DIR=$PWD
    cd /sw/src
    for line in $lines; \
    do
        echo python clumps2.py ${setfile} ${timeout} ${nthreads} ${prot2pdb_chunks} $line ${ttype} ${sampler} ${sampleMutFreq} ${sampleMutSpectra} ${coverage_track}; \
        python clumps2.py ${setfile} ${timeout} ${nthreads} ${prot2pdb_chunks} $line ${ttype} ${sampler} ${sampleMutFreq} ${sampleMutSpectra} ${coverage_track}; \
    done

    tar czf clumpsOut.tar.gz /sw/res
    mv -v clumpsOut.tar.gz $START_DIR

    """

    output_patterns = {
        "run_outdir" : "clumpsOut.tar.gz"
    }
    
    docker = CLUMPS_DOCKER_IMAGE

class clumps_postprocess_task(wolf.Task):
    # Generates summary files from array outputs of clumps.
    resources = { "mem" : "8G" }
    
    inputs = {
        "mutationsSplitByProtein" : None,
        "clumpsCandidatesOut" : None, # this is an array of directories, which in turn contain multiple files
        "huniprot2pdb" : None,
        "clumpsScanOut": None,
        "clumpsCandidatesOut" : None,
        "cancerGeneList" : None,
        "ttype" : None,
        "setfile" : None
        
    }
    
    script = """
        
        
        for file in `cat ${clumpsScanOut} | grep -v nan`
        do
            tar xzvf $file
        done
        mv sw/res/clumps /sw/res/
        
        
        mv ${mutationsSplitByProtein} /sw/dat/
        python /sw/src/clumps_postprocess.py ${setfile}  /sw/res/clumps ${cancerGeneList} ${huniprot2pdb} ${ttype}


    """
    
    # Output file from CLUMPS with list of genes
    output_patterns = {
        "clumps_output" : "clumps_output.tsv"
    }
    
    # Docker Image
    docker = CLUMPS_DOCKER_IMAGE

###### workflow
def clumps_workflow(
        maf,
        sampler,
        genome_2bit="gs://sa-clumps2-ref/dat/hg19.2bit",
        fasta="gs://sa-clumps2-ref/dat/UP000005640_9606.fasta.gz",
        pdb_dir="gs://sa-clumps2-ref/dat/pdbs/ftp.wwpdb.org/pub/pdb/data/structures/divided/pdb",
        setfile="/home/adunford/data/14k.run4", #specifies permutations,
        coverage_track="gs://sa-clumps2-ref/dat/cov/WEx_cov.fwb",
        coverage_track_index="gs://sa-clumps2-ref/dat/cov/WEx_cov.fwi",
        cancer_genes="gs://sa-clumps2-ref/dat/allCancerGenes.txt",
        uniprot_map="gs://sa-clumps2-ref/dat/huniprot/huniprot2pdb.run18.filt.txt",
        ttype="PanCan",
        scatterwidth=350,
        lineId = -1,
        threads=16,
):
    localization_results = wolf.LocalizeToDisk(
        files={
            'genome_2bit': genome_2bit,
            'fasta': fasta,
            'pdb_dir': pdb_dir,
            'coverage_track': coverage_track,
            'coverage_track_index': coverage_track_index,
            'cancer_genes': cancer_genes,
            'uniprot_map': uniprot_map
        }
    )

    clumps_prep_results = clumps_prep_task(
        inputs=dict(
            inMaf=maf,
            genome_2bit=localization_results['genome_2bit'],
            fasta=localization_results['fasta'],
            scatterWidth=scatterwidth,
            huniprot2pdb=localization_results['uniprot_map']
        )
    )

    clumps_results = clumps_run_task(
        inputs=dict(
            mutationsTarball=clumps_prep_results['mutations'],
            sampleMutFreq=clumps_prep_results['sampleMutFreq'],
            sampleMutSpectra=clumps_prep_results['sampleMutSpectra'],
            setfile=setfile,
            prot2pdb_chunks=clumps_prep_results['prot2pdbchunks'],
            pdb_dir=localization_results['pdb_dir'],
            coverage_track=localization_results['coverage_track'],
            coverage_track_index=localization_results['coverage_track_index'], # not actually used as an input; just needs to be localized alongside coverage_track
            genome_2bit=localization_results['genome_2bit'],
            fasta=localization_results['fasta'],
            sampler=sampler,
            lineId=lineId,
            nthreads=threads,
            ttype=ttype,
            timeout=timeout

        )
    )

    clumps_post_results = clumps_postprocess_task(
        inputs=dict(
            mutationsSplitByProtein=clumps_prep_results['mutations'],
            clumpsCandidatesOut=[clumps_results['run_outdir']],
            # this is an array of directories, which in turn contain multiple files
            huniprot2pdb=localization_results['uniprot_map'],
            clumpsScanOut=[clumps_results['run_outdir']],
            cancerGeneList=localization_results['cancer_genes'],
            setfile=setfile,
            ttype=ttype
        )
    )



