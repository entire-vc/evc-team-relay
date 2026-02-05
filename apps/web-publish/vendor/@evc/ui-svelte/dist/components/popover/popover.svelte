<script lang="ts">
  import { Popover as PopoverPrimitive } from 'bits-ui';

  type Props = PopoverPrimitive.RootProps;

  let { children, ...restProps }: Props = $props();
</script>

<PopoverPrimitive.Root {...restProps}>
  {@render children?.()}
</PopoverPrimitive.Root>
