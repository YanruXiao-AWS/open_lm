# This is from open_clip.
import os
import logging
import torch
import torch.distributed as dist

try:
    import torch_xla.core.xla_model as xm
    USE_XLA = True
except:
    USE_XLA = False


def is_global_master(args):
    return args.rank == 0


def is_local_master(args):
    return args.local_rank == 0


def is_master(args, local=False):
    if USE_XLA:
        return xm.is_master_ordinal(local=local)
    else:
        return is_local_master(args) if local else is_global_master(args)


def is_using_distributed():
    if "WORLD_SIZE" in os.environ:
        return int(os.environ["WORLD_SIZE"]) > 1
    if "SLURM_NTASKS" in os.environ:
        return int(os.environ["SLURM_NTASKS"]) > 1
    return False


def world_info_from_env():
    local_rank = 0
    for v in (
        "LOCAL_RANK",
        "MPI_LOCALRANKID",
        "SLURM_LOCALID",
        "OMPI_COMM_WORLD_LOCAL_RANK",
    ):
        if v in os.environ:
            local_rank = int(os.environ[v])
            break
    global_rank = 0
    for v in ("RANK", "PMI_RANK", "SLURM_PROCID", "OMPI_COMM_WORLD_RANK"):
        if v in os.environ:
            global_rank = int(os.environ[v])
            break
    world_size = 1
    for v in ("WORLD_SIZE", "PMI_SIZE", "SLURM_NTASKS", "OMPI_COMM_WORLD_SIZE"):
        if v in os.environ:
            world_size = int(os.environ[v])
            break

    return local_rank, global_rank, world_size


def init_distributed_device(args):
    # Distributed training = training on more than one GPU.
    # Works in both single and multi-node scenarios.
    args.distributed = False
    args.world_size = 1
    args.rank = 0  # global rank
    args.local_rank = 0
    args.world_group = None
    if args.dist_backend=="xla":
        import torch_xla.core.xla_model as xm
        import torch_xla.distributed.xla_backend
        os.environ['XLA_USE_BF16'] = '1' 
        # os.environ['NEURON_CC_FLAGS'] = os.environ.get('NEURON_CC_FLAGS', '') + ' --no_cache' + ' --log_level=ERROR' + ' -O1'
        os.environ['NEURON_CC_FLAGS'] = os.environ.get('NEURON_CC_FLAGS', '') + ' --log_level=ERROR --cache_dir=../compiler_cache'  + ' -O1'
    # For testing, allow forcing distributed mode to test distributed code path even on one gpu.
    if is_using_distributed() or args.force_distributed:
        if "SLURM_PROCID" in os.environ:
            # DDP via SLURM
            args.local_rank, args.rank, env_world_size = world_info_from_env()
            if args.preset_world_size is None:
                args.world_size = env_world_size
            else:
                args.world_size = args.preset_world_size
                if args.rank >= args.world_size:
                    logging.info(f"Rank {args.rank} not needed with world size {args.world_size}. Exiting.")
                    exit(0)

            # SLURM var -> torch.distributed vars in case needed
            os.environ["LOCAL_RANK"] = str(args.local_rank)
            os.environ["RANK"] = str(args.rank)
            os.environ["WORLD_SIZE"] = str(args.world_size)
            args.world_group = torch.distributed.init_process_group(
                backend=args.dist_backend,
                init_method=args.dist_url,
                world_size=args.world_size,
                rank=args.rank,
            )
        else:
            # DDP via torchrun, torch.distributed.launch
            # Note that this currently assumes that the world size is all gpus in a node.
            assert args.preset_world_size is None, "--preset_world_size with torchrun is not currently supported."
            args.local_rank, _, _ = world_info_from_env()

            if args.dist_backend=="xla":
                args.world_size = xm.xrt_world_size()
                args.rank = xm.get_ordinal()
                # print("args.rank: ", args.rank)
            else:
                args.world_group = torch.distributed.init_process_group(
                    backend=args.dist_backend, init_method=args.dist_url
                )
                # print("args world_group: ", args.world_group, "x"*1000)
                args.world_size = torch.distributed.get_world_size()
                args.rank = torch.distributed.get_rank()
                # print("args.rank2: ", args.rank)
        args.distributed = True
    # print("args.rank -- not distributed:", args.rank)
    if torch.cuda.is_available():
        if args.distributed and not args.no_set_device_rank:
            device = "cuda:%d" % args.local_rank
        else:
            device = "cuda:0"
        torch.cuda.set_device(device)
    elif args.dist_backend=="xla":
        device = xm.xla_device()
    else:
        device = "cpu"
    args.device = device
    device = torch.device(device)
    return device


def broadcast_object(args, obj, src=0):
    if args.rank == src:
        objects = [obj]
        # print("obj src"+"#"*100)
    else:
        objects = [None]
        # print("obj NONE "+"#"*100)
    # print("obj: ", obj, "*"*100)
    dist.broadcast_object_list(objects, src=src)
    return objects[0]


def all_gather_object(args, obj, dst=0):
    # gather a pickle-able python object across all ranks
    objects = [None for _ in range(args.world_size)]
    dist.all_gather_object(objects, obj)
    return objects
