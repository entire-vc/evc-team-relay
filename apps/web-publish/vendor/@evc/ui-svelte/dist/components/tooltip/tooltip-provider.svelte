<script lang="ts">
  import { Tooltip as TooltipPrimitive } from 'bits-ui';

  type Props = TooltipPrimitive.ProviderProps;

  let { children, ...restProps }: Props = $props();
</script>

<TooltipPrimitive.Provider {...restProps}>
  {@render children?.()}
</TooltipPrimitive.Provider>
