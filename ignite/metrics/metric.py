from __future__ import annotations
import numbers
from abc import ABCMeta, abstractmethod
from functools import wraps
from collections.abc import Mapping
import warnings

from typing import Callable, Union, Optional, Any

import torch
import torch.distributed as dist

from ignite.engine import Events, Engine

__all__ = [
    'Metric'
]


class Metric(metaclass=ABCMeta):
    """
    Base class for all Metrics.

    Args:
        output_transform (callable, optional): a callable that is used to transform the
            :class:`~ignite.engine.Engine`'s `process_function`'s output into the
            form expected by the metric. This can be useful if, for example, you have a multi-output model and
            you want to compute the metric with respect to one of the outputs.
            By default, metrics require the output as `(y_pred, y)` or `{'y_pred': y_pred, 'y': y}`.
        device (str of torch.device, optional): device specification in case of distributed computation usage.
            In most of the cases, it can be defined as "cuda:local_rank" or "cuda"
            if already set `torch.cuda.set_device(local_rank)`. By default, if a distributed process group is
            initialized and available, device is set to `cuda`.

    """
    _required_output_keys = ("y_pred", "y")

    def __init__(self, output_transform: Callable = lambda x: x,
                 device: Optional[Union[str, torch.device]] = None):
        self._output_transform = output_transform

        # Check device if distributed is initialized:
        if dist.is_available() and dist.is_initialized():

            # check if reset and update methods are decorated. Compute may not be decorated
            if not (hasattr(self.reset, "_decorated") and hasattr(self.update, "_decorated")):
                warnings.warn("{} class does not support distributed setting. Computed result is not collected "
                              "across all computing devices".format(self.__class__.__name__),
                              RuntimeWarning)
            if device is None:
                device = "cuda"
            device = torch.device(device)
        self._device = device
        self._is_reduced = False
        self.reset()

    @abstractmethod
    def reset(self) -> None:
        """
        Resets the metric to it's initial state.

        This is called at the start of each epoch.
        """
        pass

    @abstractmethod
    def update(self, output) -> None:
        """
        Updates the metric's state using the passed batch output.

        This is called once for each batch.

        Args:
            output: the is the output from the engine's process function.
        """
        pass

    @abstractmethod
    def compute(self) -> Any:
        """
        Computes the metric based on it's accumulated state.

        This is called at the end of each epoch.

        Returns:
            Any: the actual quantity of interest.

        Raises:
            NotComputableError: raised when the metric cannot be computed.
        """
        pass

    def _sync_all_reduce(
            self,
            tensor: Union[torch.Tensor, numbers.Number]) -> Union[torch.Tensor,
                                                                  numbers.Number]:
        if not (dist.is_available() and dist.is_initialized()):
            # Nothing to reduce
            return tensor

        tensor_to_number = False
        if isinstance(tensor, numbers.Number):
            tensor = torch.tensor(tensor, device=self._device)
            tensor_to_number = True

        if isinstance(tensor, torch.Tensor):
            # check if the tensor is at specified device
            if tensor.device != self._device:
                tensor = tensor.to(self._device)
        else:
            raise TypeError("Unhandled input type {}".format(type(tensor)))

        # synchronize and reduce
        dist.barrier()
        dist.all_reduce(tensor)

        if tensor_to_number:
            return tensor.item()
        return tensor

    def started(self, engine: Engine) -> None:
        self.reset()

    @torch.no_grad()
    def iteration_completed(self, engine: Engine) -> None:
        output = self._output_transform(engine.state.output)
        if isinstance(output, Mapping):
            if self._required_output_keys is None:
                raise TypeError("Transformed engine output for {} metric should be a tuple/list, but given {}"
                                .format(self.__class__.__name__, type(output)))
            if not all([k in output for k in self._required_output_keys]):
                raise ValueError("When transformed engine's output is a mapping, "
                                 "it should contain {} keys, but given {}".format(self._required_output_keys,
                                                                                  list(output.keys())))
            output = tuple(output[k] for k in self._required_output_keys)
        self.update(output)

    def completed(self, engine: Engine, name: str) -> None:
        result = self.compute()
        if torch.is_tensor(result) and len(result.shape) == 0:
            result = result.item()
        engine.state.metrics[name] = result

    def attach(self, engine: Engine, name: str) -> None:
        engine.add_event_handler(Events.EPOCH_COMPLETED, self.completed, name)
        if not engine.has_event_handler(self.started, Events.EPOCH_STARTED):
            engine.add_event_handler(Events.EPOCH_STARTED, self.started)
        if not engine.has_event_handler(self.iteration_completed, Events.ITERATION_COMPLETED):
            engine.add_event_handler(Events.ITERATION_COMPLETED, self.iteration_completed)

    def __add__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x + y, self, other)

    def __radd__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x + y, other, self)

    def __sub__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x - y, self, other)

    def __rsub__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x - y, other, self)

    def __mul__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x * y, self, other)

    def __rmul__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x * y, other, self)

    def __pow__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x ** y, self, other)

    def __rpow__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x ** y, other, self)

    def __mod__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x % y, self, other)

    def __div__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x.__div__(y), self, other)

    def __rdiv__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x.__div__(y), other, self)

    def __truediv__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x.__truediv__(y), self, other)

    def __rtruediv__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x.__truediv__(y), other, self)

    def __floordiv__(self, other: Metric) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x, y: x // y, self, other)

    def __getattr__(self, attr: str) -> Callable:
        from ignite.metrics import MetricsLambda

        def fn(x, *args, **kwargs):
            return getattr(x, attr)(*args, **kwargs)

        def wrapper(*args, **kwargs):
            return MetricsLambda(fn, self, *args, **kwargs)

        return wrapper

    def __getitem__(self, index: Any) -> Metric:
        from ignite.metrics import MetricsLambda
        return MetricsLambda(lambda x: x[index], self)


def sync_all_reduce(*attrs) -> Callable:
    def wrapper(func: Callable) -> Callable:

        @wraps(func)
        def another_wrapper(self: Metric, *args, **kwargs) -> Callable:
            if not isinstance(self, Metric):
                raise RuntimeError("Decorator sync_all_reduce should be used on "
                                   "ignite.metric.Metric class methods only")

            if len(attrs) > 0 and not self._is_reduced:
                for attr in attrs:
                    t = getattr(self, attr, None)
                    if t is not None:
                        t = self._sync_all_reduce(t)
                        self._is_reduced = True
                        setattr(self, attr, t)

            return func(self, *args, **kwargs)

        return another_wrapper

    wrapper._decorated = True
    return wrapper


def reinit__is_reduced(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        func(self, *args, **kwargs)
        self._is_reduced = False

    wrapper._decorated = True
    return wrapper
