<script lang="ts">
  import { Tooltip as TooltipPrimitive } from 'bits-ui';

  type Props = TooltipPrimitive.RootProps;

  let { children, delayDuration = 200, ...restProps }: Props = $props();
</script>

<TooltipPrimitive.Root {delayDuration} {...restProps}>
  {@render children?.()}
</TooltipPrimitive.Root>
