<script lang="ts">
  import { Tooltip as TooltipPrimitive } from 'bits-ui';

  type Props = TooltipPrimitive.TriggerProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<TooltipPrimitive.Trigger {...restProps}>
  {@render children?.()}
</TooltipPrimitive.Trigger>
