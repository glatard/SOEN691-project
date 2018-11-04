from pyspark import SparkContext, SparkConf
from io import BytesIO
from time import sleep, time
import os
import socket
import uuid
import shutil
import numpy as np
import nibabel as nib
import argparse
import subprocess


def write_bench(name, start_time, end_time, node, output_dir,
                filename, benchmark_dir=None, benchmark_file=None):

    if not benchmark_file:
        assert benchmark_dir, 'benchmark_dir parameter has not been defined.'

        try:
            os.makedirs(benchmark_dir)
        except Exception as e:
            pass

        benchmark_file = os.path.join(
                benchmark_dir,
                "bench-{}.txt".format(str(uuid.uuid1()))
                )

    with open(benchmark_file, 'a+') as f:
        f.write('{0} {1} {2} {3} {4}\n'.format(name, start_time, end_time,
                                               node, filename))

    return benchmark_file


def read_img(filename, data, benchmark, start, output_dir, bench_dir=None):

    start_time = time() - start

    # load binary data into Nibabel
    fh = nib.FileHolder(fileobj=BytesIO(data))
    im = nib.Nifti1Image.from_file_map({'header': fh, 'image': fh})

    data = im.get_data()

    end_time = time() - start

    bn = os.path.basename(filename)

    bench_file = None
    if benchmark:
        bench_file = write_bench('read_img', start_time, end_time,
                                 socket.gethostname(), output_dir, bn,
                                 benchmark_dir=bench_dir)

    return (filename, data, (im.affine, im.header), bench_file)


def increment_data(filename, data, metadata, delay, benchmark, start,
                   output_dir, work_dir=None, bench_file=None, cli=False):

    start_time = time() - start

    if not cli:
        data += 1
        sleep(delay)
    else:
        work_dir = output_dir if work_dir is None else work_dir

        try:
            os.makedirs(work_dir)
        except Exception as e:
            pass

        fn = filename[5:] if 'file:' in filename else filename

        p = subprocess.Popen(['increment.py', fn,
                              work_dir, '--delay', str(delay)])
        (out, err) = p.communicate()

        fn = os.path.basename(fn)
        out_fn = 'inc-{}'.format(fn) if 'inc' not in fn else fn
        out_fp = os.path.join(work_dir, out_fn)

        filename = out_fp

    end_time = time() - start

    bench_dir = None
    if benchmark:
        if os.path.isdir(bench_file):
            bench_dir = bench_file
            bench_file = None

        bn = os.path.basename(filename)
        write_bench('increment_data', start_time, end_time,
                    socket.gethostname(), output_dir, bn,
                    bench_dir, bench_file)

    if bench_file is not None:
        return (filename, data, metadata, bench_file)
    else:
        return (filename, data, metadata, bench_dir)


def save_incremented(filename, data, metadata, benchmark, start,
                     output_dir, iterations, bench_file=None, cli=False):

    start_time = time() - start

    bn = os.path.basename(filename)
    out_fn = os.path.join(output_dir, 'inc{0}-{1}'.format(iterations, bn))

    if not cli:
        im = nib.Nifti1Image(data, metadata[0], header=metadata[1])
        nib.save(im, out_fn)
    else:
        shutil.copyfile(filename, out_fn)

    end_time = time() - start

    if benchmark:
        bench_dir = None
        if os.path.isdir(bench_file):
            bench_dir = bench_file
            bench_file = None
        write_bench('save_incremented', start_time, end_time,
                    socket.gethostname(), output_dir, bn,
                    benchmark_dir=bench_dir, benchmark_file=bench_file)

    return (out_fn, 'SUCCESS')


def main():

    start = time()

    parser = argparse.ArgumentParser(description="BigBrain incrementation")
    parser.add_argument('bb_dir', type=str,
                        help=('The folder containing BigBrain NIfTI images'
                              '(local fs only)'))
    parser.add_argument('output_dir', type=str,
                        help=('the folder to save incremented images to'
                              '(local fs only)'))
    parser.add_argument('iterations', type=int, help='number of iterations')
    parser.add_argument('--delay', type=int, default=0,
                        help='task duration time (in s)')
    parser.add_argument('--benchmark', action='store_true',
                        help='benchmark results')
    parser.add_argument('--cli', action='store_true',
                        help='use cli program')
    parser.add_argument('--work_dir', type=str, help="working directory")

    args = parser.parse_args()

    conf = SparkConf().setAppName("Spark BigBrain incrementation")
    sc = SparkContext.getOrCreate(conf=conf)

    delay = args.delay

    output_dir = os.path.abspath(args.output_dir)
    try:
        os.makedirs(output_dir)
    except Exception as e:
        pass

    app_uuid = str(uuid.uuid1())
    print('Application id: ', app_uuid)
    benchmark_dir = os.path.join(output_dir,
                                 'benchmarks-{}'.format(app_uuid))
    try:
        print('Creating benchmark directory at ', benchmark_dir)
        os.makedirs(benchmark_dir)
    except Exception as e:
        pass

    # read binary data stored in folder and create an RDD from it
    imRDD = sc.binaryFiles('file://' + os.path.abspath(args.bb_dir))

    if not args.cli:
        imRDD = imRDD.map(lambda x: read_img(x[0], x[1],
                                             args.benchmark,
                                             start, output_dir,
                                             bench_dir=benchmark_dir))

        for i in range(args.iterations):
            imRDD = imRDD.map(lambda x: increment_data(x[0], x[1], x[2], delay,
                                                       args.benchmark, start,
                                                       output_dir,
                                                       bench_file=x[3]))
    else:
        work_dir = os.path.abspath(args.work_dir)
        for i in range(args.iterations):
            imRDD = imRDD.map(lambda x: increment_data(x[0], None, None, delay,
                                                       args.benchmark, start,
                                                       output_dir,
                                                       work_dir,
                                                       benchmark_dir,
                                                       args.cli))

    imRDD.map(lambda x: save_incremented(x[0], x[1], x[2],
                                         args.benchmark, start,
                                         output_dir,
                                         args.iterations, x[3], args.cli)) \
         .collect()

    end = time() - start

    if args.benchmark:
        fname = 'benchmark-{}.txt'.format(app_uuid)
        benchmark_file = os.path.join(output_dir, fname)
        write_bench('driver program', 0, end, socket.gethostname(),
                    output_dir, 'allfiles', benchmark_file=benchmark_file)

        with open(benchmark_file, 'a+') as bench:
            for b in os.listdir(benchmark_dir):
                with open(os.path.join(benchmark_dir, b), 'r') as f:
                    bench.write(f.read())

        shutil.rmtree(benchmark_dir)


if __name__ == '__main__':
    main()